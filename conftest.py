"""Makes `import src...` work when running pytest or the app from the project
root, without needing an editable install.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest


@pytest.fixture(autouse=True)
def _forget_recent_sheet_rows():
    """The logger refuses a row it sent minutes ago (a double click). Tests
    send the same rows on purpose, so each one starts with a clean memory."""
    from src.logging_tools import trade_logger
    trade_logger._recent_rows.clear()
    yield
    trade_logger._recent_rows.clear()
