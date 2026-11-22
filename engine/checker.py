"""Run every registered rule against a build."""
from __future__ import annotations

from engine.models import Build
from engine.rules import FAIL, PASS, RULES, WARN, Result


def check(build: Build) -> list[Result]:
    return [r.run(build) for r in RULES]


def summary(results: list[Result]) -> str:
    """FAIL if any rule failed, else WARN if any warned, else PASS (SKIPs are ignored)."""
    statuses = {r.status for r in results}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return PASS
