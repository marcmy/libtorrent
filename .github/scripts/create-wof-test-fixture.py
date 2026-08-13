#!/usr/bin/env python3

import argparse
import hashlib
from pathlib import Path


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, dict):
        return b"d" + b"".join(
            bencode(key) + bencode(value[key]) for key in sorted(value)
        ) + b"e"
    raise TypeError(type(value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", type=int, default=(4 * 1024**3) + (64 * 1024**2) + 12345)
    parser.add_argument("--piece-length", type=int, default=1024 * 1024)
    args = parser.parse_args()

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    payload = root / "payload.bin"
    torrent = root / "payload.torrent"

    # Highly compressible but non-zero deterministic data. The logical file
    # intentionally crosses 4 GiB so WOF uses its large-file chunk table.
    block = bytes(range(256)) * 4096  # 1 MiB
    pieces = []
    remaining = args.size

    with payload.open("wb", buffering=0) as out:
        while remaining:
            chunk = block[: min(len(block), remaining)]
            out.write(chunk)
            pieces.append(hashlib.sha1(chunk).digest())
            remaining -= len(chunk)

    metadata = {
        b"info": {
            b"length": args.size,
            b"name": b"payload.bin",
            b"piece length": args.piece_length,
            b"pieces": b"".join(pieces),
        }
    }
    torrent.write_bytes(bencode(metadata))

    print(f"payload={payload}")
    print(f"torrent={torrent}")
    print(f"logical_bytes={args.size}")
    print(f"pieces={len(pieces)}")


if __name__ == "__main__":
    main()
