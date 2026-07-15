#!/usr/bin/env python3
"""One-time Google OAuth token minter for the read-only google-docs/google-sheets plugins.

Run this ONCE on a machine with a browser, then mount the resulting token.json
read-only into the agent container and point GOOGLE_OAUTH_TOKEN at it.

Prereq: a Desktop OAuth client (client_secret.json) from Google Cloud Console
(APIs & Services -> Credentials -> Create OAuth client ID -> Desktop app), with
the Docs API and Sheets API enabled on the project.

Usage:
    pip install google-auth-oauthlib
    python authorize-google.py /path/to/client_secret.json /path/to/token.json

The token is minted with READ-ONLY scopes only (documents.readonly,
spreadsheets.readonly, drive.readonly). Service-account usage needs no token —
set GOOGLE_APPLICATION_CREDENTIALS instead and share the doc/sheet with the SA.
"""
import sys

SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    client_secret, token_out = sys.argv[1], sys.argv[2]
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(token_out, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"Wrote read-only token to {token_out}")
    print("Mount it read-only and set GOOGLE_OAUTH_TOKEN to its in-container path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
