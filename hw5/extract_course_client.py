#!/usr/bin/env python3
"""Extract the unmodified Python 3.12 entry-point bytecode from the supplied macOS PyInstaller executable.

Run on your Mac. The resulting .pyc runs in a Linux Python 3.12 container; no
course-client implementation is replaced or invented. Keep extracted bytecode
out of a public GitHub repository unless course redistribution is permitted.
"""
import argparse
import base64
import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path

MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'
COOKIE = struct.Struct('!8siiii64s')
TOC = struct.Struct('!iIIIBc')


def extract(executable: Path) -> tuple[bytes, str]:
    payload = executable.read_bytes()
    cookie_at = payload.rfind(MAGIC)
    if cookie_at < 0:
        raise ValueError('Not a PyInstaller executable; ask course staff for the Linux build/source')
    magic, pkglen, toc_offset, toc_length, pyver, _ = COOKIE.unpack_from(payload, cookie_at)
    if pyver != 312:
        raise ValueError(f'Expected Python 3.12 bytecode, found {pyver}')
    base = cookie_at + COOKIE.size - pkglen
    if base < 0 or not (base < base + toc_offset < cookie_at):
        raise ValueError('Corrupt PyInstaller archive')
    cursor = base + toc_offset
    end = cursor + toc_length
    code = None
    header = None
    while cursor < end:
        entry_len, off, packed, unpacked, compressed, kind = TOC.unpack_from(payload, cursor)
        if entry_len < TOC.size or cursor + entry_len > end:
            raise ValueError('Invalid PyInstaller entry')
        name = payload[cursor + TOC.size:cursor + entry_len].rstrip(b'\x00').decode('utf-8', 'replace')
        chunk = payload[base + off:base + off + packed]
        if compressed:
            chunk = zlib.decompress(chunk)
        if len(chunk) != unpacked:
            raise ValueError(f'Incorrect length for {name}')
        if kind == b's' and name == 'http-client':
            code = chunk
        if name == 'base_library.zip':
            with zipfile.ZipFile(io.BytesIO(chunk)) as archive:
                pyc_name = next((n for n in archive.namelist() if n.endswith('.pyc')), None)
                if pyc_name:
                    header = archive.read(pyc_name)[:4]
        cursor += entry_len
    if code is None or header is None:
        raise ValueError('Missing course entry point or Python 3.12 magic')
    if header != bytes.fromhex('cb0d0d0a'):
        raise ValueError(f'Unexpected Python bytecode magic {header.hex()}')
    # Header: magic, zero flags, zero timestamp + source size. Marshal bytes follow.
    pyc = header + b'\x00' * 12 + code
    return pyc, 'Python 3.12 original course-client entry point'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('--output', type=Path, default=Path('hw5/.course_client_312.b64'))
    args = parser.parse_args()
    pyc, label = extract(args.executable)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(base64.b64encode(pyc) + b'\n')
    print(f'{label}; generated {len(pyc)} bytes (base64 metadata {args.output}).')
    print('Keep this derived course file out of public GitHub; it is used only to boot your test VM.')

if __name__ == '__main__':
    main()
