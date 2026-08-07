#!/usr/bin/env python3
"""Stream only Facebook environment settings between trusted hosts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from refresh_facebook_page_token import read_env, write_env


SYNC_KEYS = (
    "FACEBOOK_APP_ID",
    "FACEBOOK_APP_SECRET",
    "FACEBOOK_USER_ACCESS_TOKEN",
    "FACEBOOK_PAGE_ACCESS_TOKEN",
    "FACEBOOK_PAGE_ID",
    "FACEBOOK_GRAPH_VERSION",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("export", "import"))
    parser.add_argument("--env", default=".env", type=Path)
    args = parser.parse_args()

    lines, values = read_env(args.env)
    if args.mode == "export":
        missing = [key for key in SYNC_KEYS if not values.get(key)]
        if missing:
            raise SystemExit(f"Missing required settings: {', '.join(missing)}")
        json.dump({key: values[key] for key in SYNC_KEYS}, sys.stdout)
        return

    payload = json.load(sys.stdin)
    updates = {key: str(payload[key]) for key in SYNC_KEYS if payload.get(key)}
    if set(updates) != set(SYNC_KEYS):
        raise SystemExit("The Facebook settings payload is incomplete.")
    write_env(args.env, lines, updates)
    print(f"facebook_settings_updated={len(updates)}")


if __name__ == "__main__":
    main()
