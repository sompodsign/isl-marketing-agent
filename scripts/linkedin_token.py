#!/usr/bin/env python3
"""Run LinkedIn's 3-legged OAuth flow and persist an access token for Page posting.

Modes:
  --authorize   Print the authorization URL to open in a browser.
  --exchange    Exchange the ?code= redirect parameter for an access token.
  --refresh     Exchange the stored refresh token for a fresh access token.
  --whoami      Show the member URN and the Company Pages this token can post to.

Required in .env for --authorize/--exchange/--refresh: LINKEDIN_CLIENT_ID,
LINKEDIN_CLIENT_SECRET. LINKEDIN_REDIRECT_URI defaults to
http://localhost:8080/linkedin/callback and must match the LinkedIn app setting.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from refresh_facebook_page_token import read_env, write_env

LINKEDIN_KEYS = ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN")
DEFAULT_REDIRECT_URI = "http://localhost:8080/linkedin/callback"
DEFAULT_SCOPES = "w_organization_social w_member_social r_organization_admin"


def api_request(url: str, data: dict | bytes | None = None, headers: dict | None = None, method: str = "GET"):
    payload = None
    request_headers = dict(headers or {})
    if isinstance(data, dict):
        payload = urllib.parse.urlencode(data).encode()
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, bytes):
        payload = data
    request = urllib.request.Request(url, data=payload, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read().decode("utf-8", errors="replace")).get("message", "")
        except (json.JSONDecodeError, AttributeError):
            message = ""
        raise RuntimeError(f"LinkedIn returned HTTP {error.code}: {message[:300]}") from error


def require(values: dict, key: str) -> str:
    value = values.get(key)
    if not value:
        raise SystemExit(f"Missing required setting: {key}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("authorize", "exchange", "refresh", "whoami"))
    parser.add_argument("--code", default="", help="The ?code= parameter LinkedIn redirected back with.")
    parser.add_argument(
        "--scope",
        default=DEFAULT_SCOPES,
        help="Space-separated scopes for --authorize. Use 'w_member_social' when only the "
        "Share on LinkedIn product is approved; add w_organization_social r_organization_admin "
        "once Community Management API is granted.",
    )
    parser.add_argument("--env", default=".env", type=Path)
    args = parser.parse_args()

    lines, values = read_env(args.env)
    client_id = values.get("LINKEDIN_CLIENT_ID", "")
    client_secret = values.get("LINKEDIN_CLIENT_SECRET", "")
    redirect_uri = values.get("LINKEDIN_REDIRECT_URI") or DEFAULT_REDIRECT_URI

    if args.mode == "authorize":
        if not client_id:
            raise SystemExit("Missing required setting: LINKEDIN_CLIENT_ID")
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": "isl-marketing-agent",
            "scope": args.scope,
        }
        print("Open this URL in a browser, sign in as the Company Page admin, and approve access:")
        print(f"https://www.linkedin.com/oauth/v2/authorization?{urllib.parse.urlencode(params)}")
        print("Then run: python scripts/linkedin_token.py exchange --code 'THE_CODE_FROM_THE_REDIRECT_URL'")
        return

    if args.mode == "exchange":
        if not (client_id and client_secret and args.code):
            raise SystemExit("--exchange needs LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, and --code.")
        token = api_request(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": args.code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
        )
        if not token.get("access_token"):
            raise SystemExit("LinkedIn did not return an access token.")
        updates = {"LINKEDIN_ACCESS_TOKEN": token["access_token"]}
        if token.get("refresh_token"):
            updates["LINKEDIN_REFRESH_TOKEN"] = token["refresh_token"]
        write_env(args.env, lines, updates)
        print(f"access_token_saved=true expires_in={token.get('expires_in', 'unknown')}s (~60 days)")
        print("Next: run `python scripts/linkedin_token.py whoami` to find LINKEDIN_AUTHOR_URN.")
        return

    if args.mode == "refresh":
        missing = [key for key in ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "LINKEDIN_REFRESH_TOKEN") if not values.get(key)]
        if missing:
            raise SystemExit(f"Missing required settings: {', '.join(missing)}")
        token = api_request(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "refresh_token",
                "refresh_token": values["LINKEDIN_REFRESH_TOKEN"],
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if not token.get("access_token"):
            raise SystemExit("LinkedIn did not return an access token.")
        updates = {"LINKEDIN_ACCESS_TOKEN": token["access_token"]}
        if token.get("refresh_token") and token["refresh_token"] != values.get("LINKEDIN_REFRESH_TOKEN"):
            updates["LINKEDIN_REFRESH_TOKEN"] = token["refresh_token"]
        write_env(args.env, lines, updates)
        print(f"access_token_refreshed=true expires_in={token.get('expires_in', 'unknown')}s")
        return

    # whoami: identify the member and the pages this token may post to.
    token = require(values, "LINKEDIN_ACCESS_TOKEN")
    headers = {"Authorization": f"Bearer {token}", "X-Restli-Protocol-Version": "2.0.0"}
    me = api_request("https://api.linkedin.com/v2/me", headers=headers)
    person_urn = f"urn:li:person:{me.get('id', '')}"
    print(f"member_urn={person_urn}")
    print("Post to a personal profile with LINKEDIN_AUTHOR_URN set to the member URN above.")
    try:
        acls = api_request(
            "https://api.linkedin.com/v2/organizationalEntityAcls?"
            + urllib.parse.urlencode(
                {
                    "q": "organizationalEntity",
                    "organizationalEntityTarget": person_urn,
                    "role": "ADMINISTRATOR",
                    "state": "APPROVED",
                }
            ),
            headers=headers,
        )
        organizations = [
            element.get("organizationalTarget", "")
            for element in acls.get("elements", [])
            if element.get("organizationalTarget", "").startswith("urn:li:organization:")
        ]
    except RuntimeError as error:
        print(f"could_not_list_pages={error}")
        print("Set LINKEDIN_AUTHOR_URN to urn:li:organization:<PAGE_ID> from your Company Page admin tools.")
        return
    if organizations:
        print("company_pages_you_administer:")
        for organization in organizations:
            print(f"  {organization}")
        print("Set LINKEDIN_AUTHOR_URN to the Company Page URN above for business-page posting.")
    else:
        print("no_company_pages_found=Create a LinkedIn Company Page and become its admin, then re-run whoami.")


if __name__ == "__main__":
    main()