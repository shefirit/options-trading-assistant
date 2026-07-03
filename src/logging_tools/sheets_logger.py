"""Logs a trade straight into your Google Sheet.

ONE-TIME SETUP (so the app can write to your sheet without a browser login):
  1. Go to https://console.cloud.google.com , create a project (any name).
  2. Enable the "Google Sheets API" for that project.
  3. Create a "Service Account", then create a JSON key for it and download it.
     Save that file as  google_credentials.json  in this project folder.
  4. Open the JSON file, copy the "client_email" value (looks like
     something@yourproject.iam.gserviceaccount.com).
  5. In your Google Sheet, click Share and give that email Editor access.
That's it - the app can now append rows. Your keys stay on your PC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.engine.config_loader import load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict[str, Any]:
    return load_settings().get("google_sheet", {}) or {}


def _credentials_path() -> Path:
    cfg = _config()
    name = cfg.get("credentials_file", "google_credentials.json")
    return PROJECT_ROOT / name


def is_configured() -> bool:
    """True only if the sheet is enabled AND the credentials file is present."""
    cfg = _config()
    return bool(cfg.get("enabled")) and _credentials_path().exists()


def sheet_url() -> str:
    sid = _config().get("spreadsheet_id", "")
    return f"https://docs.google.com/spreadsheets/d/{sid}"


def append(row: list[Any], header: list[str]) -> str:
    """Append one row to the configured worksheet. Returns the sheet URL.

    Raises if gspread is missing or the sheet cannot be reached - the caller
    (trade_logger) catches that and falls back to the local Excel file.
    """
    import gspread  # imported lazily so the app runs without it installed

    cfg = _config()
    gc = gspread.service_account(filename=str(_credentials_path()))
    sh = gc.open_by_key(cfg["spreadsheet_id"])

    gid = cfg.get("worksheet_gid")
    ws = sh.get_worksheet_by_id(int(gid)) if gid is not None else sh.sheet1

    # Write a header row first if the sheet is empty.
    if not ws.get_all_values():
        ws.append_row(header, value_input_option="USER_ENTERED")
    ws.append_row(row, value_input_option="USER_ENTERED")
    return sheet_url()
