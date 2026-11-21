"""Load config/assumptions.json - the single source of truth for every number."""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "assumptions.json"
DATA = ROOT / "data"
DERIVED = DATA / "derived"
WEATHER = DATA / "weather"
RESULTS = ROOT / "results"

# Scenario overrides are local to the current execution context. A what-if run
# can change an assumption without rewriting assumptions.json or leaking the
# value into another simulation in the same process.
_OVERRIDES: ContextVar[dict[str, dict[str, Any]]] = ContextVar(
    "heatshift_assumption_overrides", default={}
)


@lru_cache(maxsize=1)
def assumptions() -> dict:
    return json.loads(CONFIG.read_text())


def val(path: str):
    """val('server.heat_recovery') -> 0.8"""
    override = _OVERRIDES.get().get(path)
    if override is not None:
        return override["value"]
    node = assumptions()
    for part in path.split("."):
        node = node[part]
    return node["value"] if isinstance(node, dict) and "value" in node else node


@contextmanager
def scenario(overrides: dict[str, Any], *, source: str = "what-if") -> Iterator[None]:
    """Run a what-if scenario with isolated, auditable overrides.

    Example::

        with scenario({"reference_house.tank_usable_kwh": 70}, source="double tank"):
            run_year()

    The normal JSON configuration is never modified. Nested scenarios inherit
    outer overrides, and each changed value is available through
    :func:`assumption_audit`.
    """
    current = dict(_OVERRIDES.get())
    for path, value in overrides.items():
        original = val(path)
        current[path] = {"value": value, "source": source, "original": original}
    token = _OVERRIDES.set(current)
    try:
        yield
    finally:
        _OVERRIDES.reset(token)


def assumption_audit() -> list[dict[str, Any]]:
    """Return active overrides and their original values for reports/UI."""
    return [{"key": key, **item} for key, item in sorted(_OVERRIDES.get().items())]


def flat_assumptions() -> list[dict]:
    """Flatten into rows for the dashboard / README assumptions table."""
    rows = []
    for section, items in assumptions().items():
        if section.startswith("_"):
            continue
        for key, item in items.items():
            rows.append({
                "section": section,
                "key": key,
                "value": item.get("value"),
                "unit": item.get("unit", ""),
                "source": item.get("source", ""),
                "override": _OVERRIDES.get().get(f"{section}.{key}"),
            })
    return rows
