#!/usr/bin/env python3
"""Exchange a short-lived Facebook user token and persist a long-lived Page token."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


FACEBOOK_KEYS = {
    "FACEBOOK_APP_ID",
    "FACEBOOK_APP_SECRET",
    "FACEBOOK_USER_ACCESS_TOKEN",
    "FACEBOOK_PAGE_ACCESS_TOKEN",
    "FACEBOOK_PAGE_ID",
    "FACEBOOK_GRAPH_VERSION",
}


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text().splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return lines, values


def graph_get(version: str, path: str, params: dict[str, str]) -> dict:
    url = f"https://graph.facebook.com/{version}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error", {}).get("message", "")
        except (json.JSONDecodeError, AttributeError):
            message = ""
        raise RuntimeError(f"Meta Graph API returned HTTP {error.code}: {message[:300]}") from error


def debug_token(version: str, token: str, app_access_token: str) -> dict:
    return graph_get(
        version,
        "debug_token",
        {"input_token": token, "access_token": app_access_token},
    ).get("data", {})


def write_env(path: Path, lines: list[str], updates: dict[str, str]) -> None:
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in updates:
                output.append(f"{key}={updates[key]}")
                written.add(key)
                continue
        output.append(line)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as temporary:
            temporary.write("\n".join(output) + "\n")
        os.chmod(temporary_name, path.stat().st_mode)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env", type=Path)
    args = parser.parse_args()

    lines, values = read_env(args.env)
    missing = sorted(key for key in FACEBOOK_KEYS if not values.get(key))
    if missing:
        raise SystemExit(f"Missing required settings: {', '.join(missing)}")

    version = values["FACEBOOK_GRAPH_VERSION"]
    app_id = values["FACEBOOK_APP_ID"]
    app_secret = values["FACEBOOK_APP_SECRET"]
    short_user_token = values["FACEBOOK_USER_ACCESS_TOKEN"]
    page_id = values["FACEBOOK_PAGE_ID"]
    app_access_token = f"{app_id}|{app_secret}"

    initial_debug = debug_token(version, short_user_token, app_access_token)
    if not initial_debug.get("is_valid") or initial_debug.get("type") != "USER":
        raise SystemExit("FACEBOOK_USER_ACCESS_TOKEN must be a valid personal USER token.")
    if str(initial_debug.get("app_id")) != app_id:
        raise SystemExit("The User token was issued by a different Meta app.")

    exchange = graph_get(
        version,
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_user_token,
        },
    )
    long_user_token = exchange.get("access_token", "")
    if not long_user_token:
        raise SystemExit("Meta did not return a long-lived User token.")

    accounts = graph_get(
        version,
        "me/accounts",
        {
            "access_token": long_user_token,
            "fields": "id,name,access_token,tasks",
        },
    ).get("data", [])
    page = next((item for item in accounts if str(item.get("id")) == page_id), None)
    if not page:
        raise SystemExit("The configured FACEBOOK_PAGE_ID was not returned by /me/accounts.")
    if "CREATE_CONTENT" not in page.get("tasks", []):
        raise SystemExit("The selected Page does not grant the CREATE_CONTENT task.")

    page_token = page.get("access_token", "")
    user_debug = debug_token(version, long_user_token, app_access_token)
    page_debug = debug_token(version, page_token, app_access_token)
    if not user_debug.get("is_valid") or user_debug.get("type") != "USER":
        raise SystemExit("Long-lived User token validation failed.")
    if not page_debug.get("is_valid") or page_debug.get("type") != "PAGE":
        raise SystemExit("Long-lived Page token validation failed.")
    if page_debug.get("expires_at") not in (0, None):
        raise SystemExit("Meta returned a Page token that still has a fixed expiration date.")

    write_env(
        args.env,
        lines,
        {
            "FACEBOOK_USER_ACCESS_TOKEN": long_user_token,
            "FACEBOOK_PAGE_ACCESS_TOKEN": page_token,
            "FACEBOOK_PAGE_ID": str(page["id"]),
        },
    )
    print(f"page_id={page['id']}")
    print("create_content=yes")
    print("user_token_type=USER")
    print(f"user_expires_at={user_debug.get('expires_at', 'unknown')}")
    print("page_token_type=PAGE")
    print("page_expires_at=none")


if __name__ == "__main__":
    main()
