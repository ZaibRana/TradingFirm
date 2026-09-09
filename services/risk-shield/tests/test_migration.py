"""Part 3.1 — 005_risk.sql, asserted as file text only.

Deliberately no Postgres connection (decision 10, option a): the real
apply-and-rerun is scripts/dev-db.sh, an acceptance step whose output goes
in the completion report. These tests guard the two properties that a
successful one-off apply would not prove — that the file is re-runnable and
that it creates what 3.6 expects.

The file arrives through the read-only /migrations mount on the dev twin.
"""

import os
import re

import pytest

MIGRATIONS_DIR = os.environ.get("MIGRATIONS_DIR", "/migrations")
FILENAME = "005_risk.sql"


@pytest.fixture(scope="module")
def sql():
    path = os.path.join(MIGRATIONS_DIR, FILENAME)
    if not os.path.exists(path):
        pytest.skip(f"{path} not mounted (run inside tf-risk-shield-dev)")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _statements(sql: str) -> str:
    """The file with `--` comment lines removed. An assertion about what
    the migration *does* must not read the prose explaining it: the first
    run of test_migration_005_creates_macro_briefs failed on the word
    user_id inside the comment that explains why there is no user_id
    column."""
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def test_migration_005_creates_macro_briefs(sql):
    assert re.search(r"CREATE SCHEMA IF NOT EXISTS risk;", sql)
    assert re.search(r"CREATE TABLE IF NOT EXISTS risk\.macro_briefs", sql)
    for column in ("id", "generated_at", "regime", "health_score", "brief_text", "inputs"):
        assert re.search(rf"^\s+{column}\s+\S", sql, re.MULTILINE), column
    assert "gen_random_uuid()" in sql
    assert "brief_text      TEXT NOT NULL" in sql
    assert "JSONB NOT NULL DEFAULT '{}'::jsonb" in sql
    # No user_id: a macro brief is one shared market view (D18, decision 2).
    assert not re.search(r"\buser_id\b", _statements(sql))


def test_migration_005_regime_check_matches_health_checks(sql):
    """One vocabulary across risk.health_checks and risk.macro_briefs."""
    regimes = "('HEALTHY', 'CAUTIOUS', 'DANGER', 'CRITICAL')"
    assert f"CHECK (regime IN {regimes})" in sql
    assert "CHECK (health_score >= 0 AND health_score <= 100)" in sql

    with open(os.path.join(MIGRATIONS_DIR, "001_initial_schema.sql"), encoding="utf-8") as fh:
        first = fh.read()
    assert f"CHECK (regime IN {regimes})" in first


def test_migration_005_creates_generated_at_index(sql):
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_macro_briefs_generated_at\s+ON risk\.macro_briefs\(generated_at DESC\)",
        sql,
    )


def test_migration_005_text_is_rerunnable(sql):
    """Every CREATE guarded, no seed INSERT (the migration rule)."""
    sql = _statements(sql)
    creates = re.findall(r"^\s*CREATE\s+(?:\w+\s+)*?(?=\w)", sql, re.MULTILINE | re.IGNORECASE)
    assert creates, "expected at least one CREATE statement"
    for stmt in re.findall(r"^\s*CREATE\b.*$", sql, re.MULTILINE | re.IGNORECASE):
        assert "IF NOT EXISTS" in stmt.upper(), stmt
    assert not re.search(r"^\s*INSERT\b", sql, re.MULTILINE | re.IGNORECASE)
    assert not re.search(r"^\s*(DROP|ALTER|TRUNCATE|DELETE)\b", sql, re.MULTILINE | re.IGNORECASE)
