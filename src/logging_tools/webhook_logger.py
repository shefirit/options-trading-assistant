"""Logs a trade to your Google Sheet through a tiny Apps Script "web app".

Why this way: it needs NO Google Cloud Console, NO JSON key files, and NO
service accounts. You paste a small script into your own sheet, deploy it, and
paste the one link it gives you into the app (Connect Google Sheet in the
sidebar). The app then just sends each trade to that link.

The link is saved in google_sheet_webhook.txt (kept out of git).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Optional

from src.engine.config_loader import load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEBHOOK_FILE = PROJECT_ROOT / "google_sheet_webhook.txt"


def get_url() -> Optional[str]:
    """The Apps Script web-app link. In the hosted (cloud) app this comes from
    Streamlit's secure secrets; when you run locally it comes from the saved file."""
    # Cloud: a secret named google_sheet_webhook (set in the Streamlit dashboard).
    try:
        import streamlit as st
        secret = st.secrets.get("google_sheet_webhook")
        if secret:
            return str(secret).strip() or None
    except Exception:
        pass
    # Local: the file saved by the sidebar "Connect Google Sheet" box.
    if WEBHOOK_FILE.exists():
        url = WEBHOOK_FILE.read_text(encoding="utf-8").strip()
        return url or None
    return None


def set_url(url: str) -> None:
    """Save the link the user pasted (called from the sidebar Connect button)."""
    WEBHOOK_FILE.write_text(url.strip(), encoding="utf-8")


def is_configured() -> bool:
    url = get_url()
    return bool(url) and url.startswith("https://")


def sheet_url() -> str:
    sid = (load_settings().get("google_sheet", {}) or {}).get("spreadsheet_id", "")
    return f"https://docs.google.com/spreadsheets/d/{sid}"


def append(row: list[Any], header: list[str]) -> str:
    """Send one row to the Apps Script web app. Returns the sheet's URL.

    Raises on any network / permission problem, so the caller can fall back to
    the local Excel backup.
    """
    url = get_url()
    if not url:
        raise RuntimeError("No Google Sheet link saved yet.")

    payload = json.dumps({"row": row, "header": header}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Apps Script answers with a redirect to googleusercontent; the append has
    # already run server-side by then. A clean 2xx here means it worked.
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Sheet web app returned HTTP {resp.status}")
    return sheet_url()


def delete_trade(trade_id: str) -> int:
    """Ask the sheet to delete every row for one trade. Returns how many rows
    were removed. Needs the v3 LogTrade.gs; raises on an older script so the
    caller can tell the user to update it."""
    url = get_url()
    if not url:
        raise RuntimeError("No Google Sheet link saved yet.")
    payload = json.dumps({"action": "delete", "trade_id": trade_id}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(
            "The sheet script is an older version without delete. Paste the "
            "updated LogTrade.gs into Apps Script and deploy a new version.")
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error", "Sheet web app returned an error.")))
    return int(data.get("deleted", 0))


def fetch_rows() -> tuple[list[str], list[list[Any]]]:
    """Read the whole trade log back from the sheet: (header, data rows).

    Needs the updated LogTrade.gs script (the one with doGet returning rows).
    Raises on any network problem or if the script is the old version, so the
    caller can fall back to the local Excel log.
    """
    url = get_url()
    if not url:
        raise RuntimeError("No Google Sheet link saved yet.")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(url + sep + "mode=rows", method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(
            "The sheet script is an older version without read-back. Paste the "
            "updated LogTrade.gs into Apps Script and deploy a new version."
        )
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error", "Sheet web app returned an error.")))
    header = [str(c) for c in data.get("header", [])]
    return header, data.get("rows", [])
