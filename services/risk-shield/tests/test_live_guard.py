"""Part 3.2 — the canary isolation guard."""

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
