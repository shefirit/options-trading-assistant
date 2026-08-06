"""No module may reference a name that does not exist.

This exists because one did, and it reached Rita's screen:

    NameError: name '_bp_effect_input' is not defined
    ui/trades/quick_log.py, line 297

The ui/trades extraction moved that helper to components.bp_effect_input and
rewrote the call sites in app.py - but not in the modules it had just moved out
of app.py. The only caller left was inside Quick Log's draft-preview branch,
which no test pressed the button for, so it shipped.

Python cannot catch this at import time: a NameError inside a function body
only fires when that line runs. Streamlit then swallows it into a friendly
"this section hit a snag" panel, so it does not even fail loudly.

symtable is stdlib, so this needs no new dependency on a cloud-deployed app
where every requirement is pinned exactly.
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PACKAGES = ("ui", "src")

# Names that genuinely appear only at runtime.
ALLOWED = {"__file__", "__name__", "__doc__", "_"}


def _module_files():
    for pkg in PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            if "__pycache__" in path.parts or "worktrees" in path.parts:
                continue
            yield path
    yield ROOT / "app.py"


def _bound_anywhere(table, found=None):
    """Every name bound in this scope or any scope nested inside it.

    A helper defined at module level is visible to a function; a name bound in
    an enclosing function is visible to a closure. Collecting the union and
    checking membership is deliberately permissive - this test is here to catch
    a name that exists NOWHERE, not to police scoping.
    """
    found = found if found is not None else set()
    for sym in table.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_parameter():
            found.add(sym.get_name())
    for child in table.get_children():
        found.add(child.get_name())
        _bound_anywhere(child, found)
    return found


def _undefined(table, known, out):
    for sym in table.get_symbols():
        name = sym.get_name()
        if sym.is_assigned() or sym.is_imported() or sym.is_parameter():
            continue
        if name in known or name in ALLOWED or hasattr(builtins, name):
            continue
        out.append(f"{table.get_name()}: {name}")
    for child in table.get_children():
        _undefined(child, known, out)
    return out


@pytest.mark.parametrize("path", list(_module_files()),
                         ids=lambda p: str(p.relative_to(ROOT)).replace("\\", "/"))
def test_module_references_no_name_that_does_not_exist(path):
    src = path.read_text(encoding="utf-8")
    top = symtable.symtable(src, str(path), "exec")
    known = _bound_anywhere(top)
    missing = _undefined(top, known, [])
    assert not missing, (
        f"{path.relative_to(ROOT)} uses names that are never defined or "
        f"imported anywhere in it: {missing}")
