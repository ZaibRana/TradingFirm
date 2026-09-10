"""
Isolation guard for risk-shield live canaries (Part 3.2 decision 11).

Called first by tests/fred_live.py and tests/quotes_live.py. A canary must
run in a throwaway container that cannot reach prod Postgres or prod Redis;
if the environment says otherwise it exits 2 before importing anything that
reads settings. Not a test file (not collected: name is not test_*).
"""

import os
import sys
from typing import Mapping, Optional


def isolation_problems(environ: Mapping[str, str]) -> list[str]:
    problems = []
    if environ.get("DB_PASSWORD"):
        problems.append("DB_PASSWORD is set")
    if "@postgres" in environ.get("DATABASE_URL", ""):
        problems.append("DATABASE_URL points at the compose postgres host")
    if "//redis:" in environ.get("REDIS_URL", ""):
        problems.append("REDIS_URL points at the compose redis host")
    return problems


def assert_isolated_env(environ: Optional[Mapping[str, str]] = None) -> None:
    problems = isolation_problems(os.environ if environ is None else environ)
    if problems:
        print("REFUSING to run a live canary: " + "; ".join(problems), file=sys.stderr)
        sys.exit(2)
