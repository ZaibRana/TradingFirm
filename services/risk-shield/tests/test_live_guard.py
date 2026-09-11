"""Part 3.2 — the canary isolation guard. Part 3.4b adds the futures canary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.live_guard import assert_isolated_env, isolation_problems

ISOLATED = {
    "DATABASE_URL": "postgresql://canary@127.0.0.1:1/tradingfirm_dev",
    "REDIS_URL": "redis://127.0.0.1:1/1",
    "FRED_API_KEY": "x" * 32,
}


def test_live_guard_accepts_isolated_env():
    assert isolation_problems(ISOLATED) == []
    assert_isolated_env(ISOLATED)   # does not exit


@pytest.mark.parametrize(
    "override",
    [
        {"DB_PASSWORD": "hunter2"},
        {"DATABASE_URL": "postgresql+asyncpg://tf_user:pw@postgres:5432/tradingfirm"},
        {"REDIS_URL": "redis://redis:6379"},
    ],
    ids=["db_password", "prod_database_url", "prod_redis_url"],
)
def test_live_guard_refuses_prod_env(override, capsys):
    env = {**ISOLATED, **override}
    with pytest.raises(SystemExit) as exc:
        assert_isolated_env(env)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "REFUSING" in err
    assert "hunter2" not in err and "pw@" not in err   # names problems, not values


def test_futures_live_refuses_prod_env():
    """Part 3.4b step 0: the futures canary exits 2 in a prod-like environment,
    before anything imports yfinance. The source order is asserted first, so a
    missing guard call fails here rather than in a subprocess that could
    download."""
    service_root = Path(__file__).resolve().parents[1]
    source = (service_root / "tests" / "futures_live.py").read_text()
    entry = source.index("def main()")
    assert source.index("assert_isolated_env()", entry) < source.index("asyncio.run(", entry)

    done = subprocess.run(
        [sys.executable, "-m", "tests.futures_live", "--label", "guard"],
        cwd=service_root, env={**os.environ, "DB_PASSWORD": "hunter2"},
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 2
    assert "REFUSING" in done.stderr and "hunter2" not in done.stderr
    assert done.stdout == ""       # nothing printed, so nothing downloaded
