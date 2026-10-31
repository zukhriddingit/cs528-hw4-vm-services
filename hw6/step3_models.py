#!/usr/bin/env python3
"""CS528 HW6: fit two held-out classifiers on normalized Cloud SQL data.

Intended to run on a short-lived Compute Engine VM (step3_vm_startup.sh).
Country classifier: client_ip only, group-held-out by IP.
Income classifier: non-target demographic/request fields, stratified row split.
Both test sets remain untouched during model selection.
"""
from __future__ import annotations

import argparse
import bisect
import ipaddress
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OrdinalEncoder
from sklearn.tree import DecisionTreeClassifier

SEED = 5286
QUERY = """
SELECT r.request_id, r.client_ip::text AS client_ip,
       CASE WHEN exc.request_id IS NOT NULL
            THEN original.country ELSE mapped.country END AS country,
       r.gender, r.age, r.income, r.time_of_day,
       r.requested_file, r.status_code, r.received_at
FROM hw6_requests AS r
JOIN hw6_ip_country AS m ON m.client_ip = r.client_ip
LEFT JOIN hw6_countries AS mapped ON mapped.country_id = m.country_id
LEFT JOIN hw6_request_country_exceptions AS exc ON exc.request_id = r.request_id
LEFT JOIN hw6_countries AS original ON original.country_id = exc.original_country_id
ORDER BY r.request_id
"""


def say(msg):
    print(msg, flush=True)


def secret_password(project: str, secret: str) -> str:
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{project}/secrets/{secret}/versions/latest"
    for attempt in range(5):
        try:
            return client.access_secret_version(name=resource).payload.data.decode("utf-8").strip()
        except Exception:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("Unreachable secret retry state")


def fetch_normalized_data(project: str, sql_instance: str, database: str,
                          user: str, secret: str) -> pd.DataFrame:
    from google.cloud.sql.connector import Connector, IPTypes
    password = secret_password(project, secret)
    connection_name = f"{project}:us-central1:{sql_instance}"
    say(f"Connecting to Cloud SQL: {connection_name} / {database}")
    for attempt in range(4):
        try:
            with Connector(refresh_strategy="LAZY") as connector:
                connection = connector.connect(
                    connection_name, "pg8000", db=database,
                    user=user, password=password, ip_type=IPTypes.PUBLIC,
                )
                try:
                    cursor = connection.cursor()
                    try:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        cursor.execute(QUERY)
                        columns = [col[0] for col in cursor.description]
                        records = []
                        while True:
                            batch = cursor.fetchmany(12000)
                            if not batch:
                                break
                            records.extend(batch)
                    finally:
                        cursor.close()
                finally:
                    connection.close()
            data = pd.DataFrame.from_records(records, columns=columns)
            say(f"Read {len(data):,} normalized request rows")
            return data
        except Exception as exc:
            if attempt == 3:
                raise
            say(f"Database read attempt {attempt + 1} failed ({type(exc).__name__}); retrying")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Unreachable connection retry state")


def ip_object(raw):
    # PostgreSQL's INET text representation often contains a /32 or /128 suffix.
    return ipaddress.ip_interface(str(raw)).ip


def ip_numeric(raw):
    obj = ip_object(raw)
    return int(obj) + (0 if obj.version == 4 else 1 << 128)


def ip_feature_frame(ip_values: pd.Series) -> pd.DataFrame:
    """Uses *only* client IP information; no country-derived features."""
    rows = []
    for raw in ip_values:
        address = ip_object(raw)
        parts = list(address.packed)
        if address.version == 4:
            parts += [0] * 12
            scaled = int(address) / (1 << 32)
        else:
            scaled = int(address) / (1 << 128)
        rows.append([address.version, scaled, *parts])
    return pd.DataFrame(rows, columns=["version", "address_fraction", *[f"byte_{i}" for i in range(16)]])


class ClosestIPClassifier:
    """Nearest observed address on the IPv4/IPv6 number line (train only)."""

    def fit(self, ip_values, countries):
        frequencies = {}
        for ip, country in zip(ip_values, countries):
            key = ip_numeric(ip)
            if key not in frequencies:
                frequencies[key] = Counter()
            frequencies[key][str(country)] += 1
        self.keys = sorted(frequencies)
        self.labels = [frequencies[key].most_common(1)[0][0] for key in self.keys]
        return self

    def predict(self, ip_values):
        output = []
        for raw in ip_values:
            key = ip_numeric(raw)
            index = bisect.bisect_left(self.keys, key)
            if index == 0:
                nearest = 0
            elif index == len(self.keys):
                nearest = len(self.keys) - 1
            elif (key - self.keys[index - 1]) <= (self.keys[index] - key):
                nearest = index - 1
            else:
                nearest = index
            output.append(self.labels[nearest])
        return np.array(output)


def make_ip_groups(ip_series, seed: int):
    unique = np.array(sorted(set(ip_series)), dtype=object)
    rng = np.random.default_rng(seed)
    rng.shuffle(unique)
    n_test = max(1, round(len(unique) * .20))
    n_val = max(1, round(len(unique) * .16))
    test_ips = set(unique[:n_test])
    val_ips = set(unique[n_test:n_test + n_val])
    test = ip_series.isin(test_ips).to_numpy()
    val = ip_series.isin(val_ips).to_numpy()
    train = ~(test | val)
    return np.flatnonzero(train), np.flatnonzero(val), np.flatnonzero(test)


def model_score(actual, predicted):
    return {
        "accuracy": round(float(accuracy_score(actual, predicted)), 6),
        "balanced_accuracy": round(float(balanced_accuracy_score(actual, predicted)), 6),
        "macro_f1": round(float(f1_score(actual, predicted, average="macro", zero_division=0)), 6),
        "n": int(len(actual)),
    }


def top_confusions(actual, predicted, limit=10):
    comparison = pd.DataFrame({"actual": np.asarray(actual), "predicted": np.asarray(predicted)})
    mistaken = comparison[comparison.actual != comparison.predicted]
    counts = mistaken.value_counts(["actual", "predicted"]).head(limit)
    return [{"actual": str(a), "predicted": str(p), "count": int(n)}
            for (a, p), n in counts.items()]


def train_country_model(df: pd.DataFrame, output: Path) -> dict:
    eligible = df.dropna(subset=["client_ip", "country"]).copy().reset_index(drop=True)
    eligible["country"] = eligible["country"].astype(str)
    tr, va, te = make_ip_groups(eligible["client_ip"], SEED)
    say(f"Country: train={len(tr):,} validation={len(va):,} test={len(te):,}; "
        f"test IPs have never appeared in training")
    X = ip_feature_frame(eligible["client_ip"])
    y = eligible["country"].to_numpy()
    scores = {}
    models = {
        "nearest_ip": ClosestIPClassifier(),
        # Bounded trees: unrestricted 198-class, 100-tree ensembles can exhaust
        # the 8 GiB RAM of e2-standard-2 before the completion marker is written.
        "decision_tree": DecisionTreeClassifier(max_leaf_nodes=4096,
                                                 min_samples_leaf=3,
                                                 random_state=SEED),
        "extra_trees": ExtraTreesClassifier(n_estimators=48, max_features=1.0,
                                            min_samples_leaf=4, max_leaf_nodes=2048,
                                            n_jobs=2, random_state=SEED),
    }
    for name, model in models.items():
        started = time.monotonic()
        if name == "nearest_ip":
            model.fit(eligible["client_ip"].iloc[tr], y[tr])
            pred = model.predict(eligible["client_ip"].iloc[va])
        else:
            model.fit(X.iloc[tr], y[tr])
            pred = model.predict(X.iloc[va])
        scores[name] = model_score(y[va], pred)
        say(f"Country candidate {name}: validation accuracy "
            f"{scores[name]['accuracy']:.2%} in {time.monotonic() - started:.1f}s")
    selected = max(scores, key=lambda name: scores[name]["accuracy"])
    final = models[selected]
    combined = np.concatenate((tr, va))
    if selected == "nearest_ip":
        final.fit(eligible["client_ip"].iloc[combined], y[combined])
        test_preds = final.predict(eligible["client_ip"].iloc[te])
    else:
        final.fit(X.iloc[combined], y[combined])
        test_preds = final.predict(X.iloc[te])
    actual = y[te]
    test_metrics = model_score(actual, test_preds)
    say(f"Country chosen: {selected}; HELD-OUT TEST accuracy={test_metrics['accuracy']:.2%}")
    pd.DataFrame({
        "request_id": eligible["request_id"].iloc[te].to_numpy(),
        "client_ip": eligible["client_ip"].iloc[te].to_numpy(),
        "true_country": actual,
        "predicted_country": test_preds,
        "correct": actual == test_preds,
    }).to_csv(output / "country_test_predictions.csv", index=False)
    return {
        "model": selected,
        "features": ["client_ip (IPv4/IPv6 numeric value and bytes)"],
        "split": "80/20 IP-group-held-out testing; 16% of IP groups for validation before final fit",
        "train_and_validation_rows": int(len(combined)),
        "test_rows": int(len(te)),
        "distinct_test_ips": int(eligible["client_ip"].iloc[te].nunique()),
        "validation_candidates": scores,
        "test": test_metrics,
        "test_confusions_top": top_confusions(actual, test_preds),
        "meets_99_percent": bool(test_metrics["accuracy"] >= .99),
    }


def income_features(data: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    n = len(data)
    X = pd.DataFrame(index=np.arange(n))
    for name in ("country", "age", "gender"):
        X[name] = data[name].fillna("unknown").astype(str).to_numpy()
    X["status_code"] = data["status_code"].fillna(-1).astype(str).to_numpy()
    keys = [ip_object(raw) for raw in data["client_ip"]]
    X["ip_version"] = [f"ipv{ip.version}" for ip in keys]
    X["ip_prefix8"] = [str(ip.packed[0]) if ip.version == 4 else f"v6-{ip.packed[0]}" for ip in keys]
    X["ip_prefix16"] = [f"{ip.packed[0]}.{ip.packed[1]}" if ip.version == 4
                         else f"v6-{ip.packed[0]}-{ip.packed[1]}" for ip in keys]
    for i in range(4):
        X[f"ip_byte_{i}"] = np.array([ip.packed[i] for ip in keys], dtype=np.float32)
    file_num = data["requested_file"].fillna("").astype(str).str.extract(r"(\d+)\.html$", expand=False)
    X["file_number"] = pd.to_numeric(file_num, errors="coerce").fillna(-1).astype(float).to_numpy()
    request_time = pd.to_datetime(data["time_of_day"], utc=True, errors="coerce")
    received_time = pd.to_datetime(data["received_at"], utc=True, errors="coerce")
    for tag, dt in (("request", request_time), ("received", received_time)):
        X[f"{tag}_hour"] = dt.dt.hour.fillna(-1).astype(float).to_numpy()
        X[f"{tag}_minute"] = dt.dt.minute.fillna(-1).astype(float).to_numpy()
        X[f"{tag}_second"] = dt.dt.second.fillna(-1).astype(float).to_numpy()
        X[f"{tag}_weekday"] = dt.dt.dayofweek.fillna(-1).astype(float).to_numpy()
        X[f"{tag}_day"] = dt.dt.day.fillna(-1).astype(float).to_numpy()
    category_columns = ["country", "age", "gender", "status_code", "ip_version",
                        "ip_prefix8", "ip_prefix16"]
    return X, category_columns


def train_income_model(df: pd.DataFrame, output: Path) -> dict:
    eligible = df.dropna(subset=["client_ip", "income"]).copy().reset_index(drop=True)
    eligible = eligible[eligible["income"].astype(str).str.strip() != ""].reset_index(drop=True)
    y = eligible["income"].astype(str).to_numpy()
    indices = np.arange(len(eligible))
    train_val, te = train_test_split(indices, test_size=.20, stratify=y, random_state=SEED)
    tr, va = train_test_split(train_val, test_size=.20, stratify=y[train_val], random_state=SEED + 1)
    say(f"Income: train={len(tr):,} validation={len(va):,} test={len(te):,}")
    X, cat_cols = income_features(eligible)
    # One proven candidate: the October 31 run measured 33.96% validation
    # accuracy with this configuration in ~858 seconds. The old second
    # depth-8 candidate never finished before the 40-minute orchestration
    # timeout. Do not burn another VM run evaluating it.
    candidates = {
        "catboost_depth6": dict(iterations=500, depth=6, learning_rate=.075, l2_leaf_reg=5),
    }
    scores = {}
    fitted = {}
    for name, cfg in candidates.items():
        started = time.monotonic()
        clf = CatBoostClassifier(loss_function="MultiClass", random_seed=SEED,
                                 thread_count=2, allow_writing_files=False,
                                 verbose=False, **cfg)
        clf.fit(X.iloc[tr], y[tr], cat_features=cat_cols,
                eval_set=(X.iloc[va], y[va]), early_stopping_rounds=65,
                use_best_model=True, verbose=False)
        pred = clf.predict(X.iloc[va]).reshape(-1)
        scores[name] = model_score(y[va], pred)
        scores[name]["best_iteration"] = int(clf.best_iteration_)
        scores[name]["elapsed_seconds"] = round(time.monotonic() - started, 2)
        fitted[name] = clf
        say(f"Income candidate {name}: validation accuracy "
            f"{scores[name]['accuracy']:.2%}, best iteration {clf.best_iteration_}, "
            f"{scores[name]['elapsed_seconds']:.1f}s")
    selected = max(scores, key=lambda name: scores[name]["accuracy"])
    best = fitted[selected]
    predictions = best.predict(X.iloc[te]).reshape(-1).astype(str)
    actual = y[te]
    baseline_label = Counter(y[tr]).most_common(1)[0][0]
    baseline_accuracy = float(np.mean(actual == baseline_label))
    test_metrics = model_score(actual, predictions)
    say(f"Income chosen: {selected}; HELD-OUT TEST accuracy={test_metrics['accuracy']:.2%}; "
        f"majority baseline={baseline_accuracy:.2%}")
    pd.DataFrame({
        "request_id": eligible["request_id"].iloc[te].to_numpy(),
        "true_income": actual,
        "predicted_income": predictions,
        "correct": actual == predictions,
    }).to_csv(output / "income_test_predictions.csv", index=False)
    importances = best.get_feature_importance()
    ranked_features = sorted(zip(X.columns, importances), key=lambda item: item[1], reverse=True)
    return {
        "model": selected,
        "features": list(X.columns),
        "split": "stratified random 64% training / 16% validation / 20% untouched test",
        "train_rows": int(len(tr)),
        "validation_rows": int(len(va)),
        "test_rows": int(len(te)),
        "validation_candidates": scores,
        "test": test_metrics,
        "majority_class_label": baseline_label,
        "majority_class_test_accuracy": round(baseline_accuracy, 6),
        "test_confusions_top": top_confusions(actual, predictions),
        "feature_importance_top": [{"name": name, "importance": round(float(val), 4)}
                                   for name, val in ranked_features[:12]],
        "meets_35_percent": bool(test_metrics["accuracy"] >= .35),
        "limitations": ("Income categories are nearly uniform; accuracy below target "
                        "suggests weak predictive signal in recorded fields. "
                        "No label leakage or test-set tuning was used."
                        if test_metrics["accuracy"] < .35 else
                        "Validated on an untouched held-out test set."),
    }


def upload_outputs(project: str, bucket_name: str, prefix: str, files: list[Path]) -> list[str]:
    from google.cloud import storage
    bucket = storage.Client(project=project).bucket(bucket_name)
    locations = []
    for path in files:
        object_name = f"{prefix.rstrip('/')}/{path.name}"
        bucket.blob(object_name).upload_from_filename(str(path))
        locations.append(f"gs://{bucket_name}/{object_name}")
    return locations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--sql-instance", default="hw5-sql")
    parser.add_argument("--database", default="hw5_requests")
    parser.add_argument("--user", default="hw5_user")
    parser.add_argument("--secret", default="hw5-db-password")
    parser.add_argument("--output-dir", default="/opt/hw6/output")
    args = parser.parse_args()

    started = time.monotonic()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    data = fetch_normalized_data(args.project, args.sql_instance, args.database,
                                 args.user, args.secret)
    if len(data) < 1000:
        raise RuntimeError(f"Expected HW5 dataset; found only {len(data)} rows")
    report = {
        "assignment": "CS528 HW6",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "database": args.database,
        "table": "hw6_requests (joined to normalized dimensions and exceptions)",
        "source_rows": int(len(data)),
        "seed": SEED,
    }

    # Save Country test-set predictions and metrics FIRST. These become
    # durable GCS artifacts even if income training later fails or times out.
    say("Training model 1/2: IP -> country")
    report["model_1_country"] = train_country_model(data, output)
    country_checkpoint = output / "country_metrics.json"
    country_checkpoint.write_text(
        json.dumps({"source_rows": len(data), "seed": SEED,
                    "model_1_country": report["model_1_country"]},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    early = upload_outputs(args.project, args.bucket, args.prefix,
                           [output / "country_test_predictions.csv", country_checkpoint])
    for uri in early:
        say(f"EARLY GCS CHECKPOINT: {uri}")

    say("Training model 2/2: request features -> income (one bounded CatBoost candidate)")
    try:
        report["model_2_income"] = train_income_model(data, output)
    except Exception as error:
        report["model_2_income"] = {"status": "failed", "error": str(error)}
        report["runtime_seconds"] = round(time.monotonic() - started, 2)
        failure_file = output / "metrics_partial.json"
        failure_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        upload_outputs(args.project, args.bucket, args.prefix, [failure_file])
        raise

    report["runtime_seconds"] = round(time.monotonic() - started, 2)
    metrics_file = output / "metrics.json"
    metrics_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    files = [output / "income_test_predictions.csv", metrics_file]
    locations = upload_outputs(args.project, args.bucket, args.prefix, files)
    say("Both sets of test predictions and the combined metrics are now in GCS:")
    for uri in early + locations:
        say(uri)
    say("Modeling completed successfully; country and income test sets were held out.")


if __name__ == "__main__":
    main()
