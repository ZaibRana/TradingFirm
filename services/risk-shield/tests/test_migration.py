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


# ── 006 (Part 3.6a, spec decision 1) ─────────────────────────────

@pytest.fixture(scope="module")
def sql_006():
    path = os.path.join(MIGRATIONS_DIR, "006_macro_brief_output.sql")
    if not os.path.exists(path):
        pytest.skip(f"{path} not mounted (run inside tf-risk-shield-dev)")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_migration_006_adds_brief_and_trigger(sql_006):
    """Two guarded ADD COLUMNs on risk.macro_briefs, brief a JSON object,
    trigger one of exactly four values, nothing else. Repeat call: every
    statement is IF NOT EXISTS (the live rerun is the part's round trip)."""
    body = _statements(sql_006)
    statements = [s.strip() for s in body.split(";") if s.strip()]
    assert len(statements) == 2
    for stmt in statements:
        assert stmt.startswith("ALTER TABLE risk.macro_briefs ADD COLUMN IF NOT EXISTS"), stmt

    brief, trigger = statements
    assert re.search(r"IF NOT EXISTS brief JSONB NOT NULL\s+CHECK \(jsonb_typeof\(brief\) = 'object'\)$", brief)
    assert re.search(r"IF NOT EXISTS trigger TEXT NOT NULL\s+CHECK \(trigger IN \(([^)]*)\)\)$", trigger)
    values = re.search(r"trigger IN \(([^)]*)\)", trigger).group(1)
    assert [v.strip().strip("'") for v in values.split(",")] == ["slot", "regime_change", "critical", "manual"]

    # No default (a default would hide a writer that forgot the field), no
    # seed, nothing destructive.
    assert "DEFAULT" not in body.upper()
    assert not re.search(r"^\s*(INSERT|DROP|TRUNCATE|DELETE|UPDATE)\b", body, re.MULTILINE | re.IGNORECASE)
    assert not re.search(r"\bDROP\b", body, re.IGNORECASE)
