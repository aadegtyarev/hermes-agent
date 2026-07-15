"""Read-only Google auth helper (shared shape across google-* plugins).

Two credential sources, both mounted read-only into the container:
  * ``GOOGLE_OAUTH_TOKEN``            — path to an authorized_user token.json
    (contains refresh_token/client_id/client_secret). Minted once with
    ``authorize.py`` (see base/plugins/README). Refresh happens in memory, so a
    read-only token file is fine.
  * ``GOOGLE_APPLICATION_CREDENTIALS`` — path to a service-account json
    (share the sheet with the SA email). Preferred when set.

Scopes are read-only by construction; the plugins request only
spreadsheets.readonly. For a hard guarantee, scope the token / service account
read-only.
"""
from __future__ import annotations

import os
from typing import List


def _sa_path() -> str:
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()


def _token_path() -> str:
    return os.getenv("GOOGLE_OAUTH_TOKEN", "").strip()


def is_configured() -> bool:
    sa, tok = _sa_path(), _token_path()
    return bool((sa and os.path.exists(sa)) or (tok and os.path.exists(tok)))


def _credentials(scopes: List[str]):
    sa = _sa_path()
    if sa and os.path.exists(sa):
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(sa, scopes=scopes)

    tok = _token_path()
    if tok and os.path.exists(tok):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(tok, scopes)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())  # in-memory; does not rewrite the RO file
        return creds

    raise RuntimeError(
        "No Google credentials. Set GOOGLE_OAUTH_TOKEN (authorized_user json) "
        "or GOOGLE_APPLICATION_CREDENTIALS (service-account json)."
    )


def service(api: str, version: str, scopes: List[str]):
    """Build a read-only googleapiclient service for *api*/*version*."""
    from googleapiclient.discovery import build

    return build(api, version, credentials=_credentials(scopes), cache_discovery=False)
