"""Every alert_type that can reach the ``alerts`` table comes from one list.

This exists because the set was written down in two places and drifted:
``risk.py`` raised ``gps_loss`` and ``search_area.py`` raised ``gps_lost`` for
the same GPS outage. Nothing crashed — ``crud.save_alert`` takes a plain str and
``notification.py`` mapped both keys to the same Thai title — but the push
cooldown is keyed on ``(patient_id, alert_type)``, so the two types got separate
cooldowns and one outage pushed the caregiver twice.

A test that spelled the strings out itself would have passed either way, so
these read them back out of the source instead. Three producers exist and all
three are scanned: a literal at the call site (``sos.py``), a module constant
(``trip_requests._DENIED_ALERT_TYPE``), and the decision dict Module 3 hands to
``risk.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.models.alert import ALERT_TYPES

BACKEND = Path(__file__).resolve().parents[1]
API_DIR = BACKEND / "app" / "api"
DECISION_ENGINE = BACKEND / "app" / "ai" / "module3_risk" / "emergency_decision_engine.py"

# The decision engine's "no emergency" sentinel. It never reaches save_alert —
# risk.py:242 gates on decision["emergency"] first — so it is not an alert type.
NON_FIRING = {"none"}


def _alert_types_in(path: Path) -> set[str]:
    """Strings assigned to ``alert_type`` in one file, however they are written."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # Module-level `NAME = "literal"`, so a constant used at the call site resolves.
    consts = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    found: set[str] = set()
    for node in ast.walk(tree):
        # alert_type=... in a call
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg != "alert_type":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
                elif isinstance(kw.value, ast.Name) and kw.value.id in consts:
                    found.add(consts[kw.value.id])
        # {"alert_type": "..."} in a dict
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant) and key.value == "alert_type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found.add(value.value)
    return found - NON_FIRING


def _alert_types_by_file() -> dict[str, set[str]]:
    sources = sorted(API_DIR.glob("*.py")) + [DECISION_ENGINE]
    found = {p.name: _alert_types_in(p) for p in sources}
    return {name: types for name, types in found.items() if types}


def test_every_alert_type_that_can_be_raised_is_in_the_canonical_list():
    unknown = {
        name: sorted(types - set(ALERT_TYPES))
        for name, types in _alert_types_by_file().items()
        if types - set(ALERT_TYPES)
    }
    assert unknown == {}, (
        f"alert types not in app.models.alert.ALERT_TYPES: {unknown}. "
        "Add it there deliberately, or fix the typo — an unknown type gets its "
        "own push cooldown and its own title fallback."
    )


def test_the_two_endpoints_that_detect_a_gps_outage_agree_on_its_name():
    """risk.py and search_area.py both fire off the same ``gap["gps_lost"]``."""
    by_file = _alert_types_by_file()

    def gps_in(name: str) -> set[str]:
        return {t for t in by_file.get(name, set()) if "gps" in t}

    assert gps_in("risk.py") == gps_in("search_area.py") != set(), (
        "One GPS outage triggers both endpoints. Different names there means "
        "two cooldowns and two pushes for a single event."
    )


def test_every_raisable_alert_type_has_a_push_title():
    """A type notification.py cannot title reaches the caregiver as 'PathGuard'."""
    from app.services.notification import _TITLES

    raised = set().union(*_alert_types_by_file().values())
    assert raised <= set(_TITLES), (
        f"no push title for {sorted(raised - set(_TITLES))} — these fall back "
        "to the bare 'PathGuard', which tells a caregiver nothing."
    )
