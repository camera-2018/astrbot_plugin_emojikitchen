#!/usr/bin/env python3
"""Build a compact Emoji Kitchen index for offline plugin startup.

The upstream metadata.json is intentionally rich because it powers a web UI.
The AstrBot plugin only needs a map from two emoji codepoints to the latest
gStaticUrl, so this script strips everything else and writes a deterministic
gzip-compressed JSON file suitable for committing to the plugin repository.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/"
    "main/app/metadata.json"
)
DEFAULT_OUTPUT = Path("assets/emoji_index.json.gz")
INDEX_FORMAT = "emoji-kitchen-index-v1"


def _read_source(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        request = Request(
            source,
            headers={
                "Accept": "application/json",
                "User-Agent": "astrbot-plugin-emojikitchen-index-builder",
            },
        )
        with urlopen(request, timeout=300) as resp:
            return resp.read()
    return Path(source).read_bytes()


def _select_combo_url(combo_list) -> str | None:
    if not combo_list:
        return None

    for combo in combo_list:
        if combo.get("isLatest", False):
            url = combo.get("gStaticUrl")
            if url:
                return url

    for combo in combo_list:
        url = combo.get("gStaticUrl")
        if url:
            return url

    return None


def build_index(metadata: dict) -> dict:
    data = metadata.get("data", {})
    index_data = {}
    combo_count = 0

    for left_cp in sorted(data):
        left_data = data.get(left_cp) or {}
        combinations = left_data.get("combinations", {})
        compact_combinations = {}

        for right_cp in sorted(combinations):
            url = _select_combo_url(combinations.get(right_cp))
            if not url:
                continue
            compact_combinations[right_cp] = url
            combo_count += 1

        if compact_combinations:
            index_data[left_cp] = {"combinations": compact_combinations}

    return {
        "format": INDEX_FORMAT,
        "knownSupportedEmoji": sorted(metadata.get("knownSupportedEmoji", [])),
        "data": index_data,
        "stats": {
            "leftEmoji": len(index_data),
            "combinations": combo_count,
        },
    }


def write_gzip_json(path: Path, payload: dict) -> None:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print(f"Reading metadata from {args.source}", file=sys.stderr)
    metadata = json.loads(_read_source(args.source).decode("utf-8"))

    print("Building compact index", file=sys.stderr)
    index = build_index(metadata)
    write_gzip_json(args.output, index)

    output_size = args.output.stat().st_size
    stats = index["stats"]
    print(
        f"Wrote {args.output} ({output_size:,} bytes, "
        f"{stats['leftEmoji']:,} left emoji, {stats['combinations']:,} combinations)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
