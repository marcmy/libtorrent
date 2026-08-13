#!/usr/bin/env python3

import argparse
import hashlib
from pathlib import Path


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(
            bencode(key) + bencode(value[key]) for key in sorted(value)
        ) + b"e"
    raise TypeError(type(value))


class PieceHasher:
    def __init__(self, piece_length):
        self.piece_length = piece_length
        self.current = hashlib.sha1()
        self.current_size = 0
        self.pieces = []

    def update(self, data):
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            take = min(self.piece_length - self.current_size, len(view) - offset)
            self.current.update(view[offset : offset + take])
            self.current_size += take
            offset += take
            if self.current_size == self.piece_length:
                self.pieces.append(self.current.digest())
                self.current = hashlib.sha1()
                self.current_size = 0

    def finish(self):
        if self.current_size:
            self.pieces.append(self.current.digest())
        return self.pieces


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--torrent", required=True)
    parser.add_argument("--piece-length", type=int, default=1024 * 1024)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    torrent = Path(args.torrent)
    files = [
        "edge/prefix.bin",
        "bulk/large-wof.bin",
        "edge/between.bin",
        "bulk/medium-wof.bin",
        "plain/tail.bin",
    ]

    hasher = PieceHasher(args.piece_length)
    file_entries = []
    total = 0

    for relative in files:
        path = data_root / Path(relative)
        size = path.stat().st_size
        with path.open("rb", buffering=0) as src:
            while True:
                chunk = src.read(4 * 1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)

        file_entries.append(
            {
                b"length": size,
                b"path": [part.encode("utf-8") for part in Path(relative).parts],
            }
        )
        total += size
        print(f"file={relative},bytes={size}")

    pieces = hasher.finish()
    metadata = {
        b"info": {
            b"files": file_entries,
            b"name": data_root.name.encode("utf-8"),
            b"piece length": args.piece_length,
            b"pieces": b"".join(pieces),
        }
    }
    torrent.write_bytes(bencode(metadata))

    print(f"torrent={torrent}")
    print(f"logical_bytes={total}")
    print(f"pieces={len(pieces)}")


if __name__ == "__main__":
    main()
