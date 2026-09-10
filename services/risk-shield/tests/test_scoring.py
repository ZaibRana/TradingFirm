"""Part 3.3 — health calculator and regime classifier. health_from_monitors
is pure and gets stubbed monitor results; compute_health runs the real
MONITORS on a real quotes view, which is what proves the view, the monitors
and the calculator agree on the contract."""

import json
import logging
from datetime import datetime, timezone

import pandas as pd
import pytest

from cache import MemoryCooldowns
from monitors import quotes
from monitors.regime import MONITORS, Monitor
from scoring import health_calculator
from scoring.health_calculator import WEIGHTS, compute_health, health_from_monitors
from scoring.regime_classifier import REGIMES, classify
from tests.fake_redis import FakeRedis
from tests.test_monitors import full_view

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)     # 10:00 EDT; bars end 09-09
LAST_AT = datetime(2026, 9, 9, 20, 30, tzinfo=timezone.utc)
KEY_QUOTES = "tf:risk:cache:quotes"
KEY_LAST = "tf:risk:cache:quotes_last"
COOL_YF = "tf:risk:cooldown:YFINANCE"

# What the real monitors score on full_view(): VIX flat 18, every other
# ticker gently rising, 1M volume — 80·25 + 60·20 + 95·20 + 65·15 + 85·10 +
# 80·10 = 7725 → 77.
FULL_VIEW_SCORES = {"vix": 80, "breadth": 60, "spy_trend": 95, "sector_rotation": 65,
                    "volume": 85, "cross_asset": 80}
FULL_VIEW_HEALTH = 77


def _now():
    return NOW


def stub(score, stale=False):
    return {"score": score, "raw": {}, "detail": "stub", "stale": stale}


def results(**scores):
    """One stub per monitor; a value is a score or (score, stale)."""
    out = {}
    for name in MONITORS:
        value = scores[name]
        out[name] = stub(*value) if isinstance(value, tuple) else stub(value)
    return out


def envelope(view, as_of):
    """A valid 3.2 quotes envelope carrying full_view()'s series."""
    return {
        "asOf": as_of.isoformat(), "period": "1y", "interval": "1d",
        "tickers": {t: {k: v for k, v in e.items() if k not in ("asOf", "stale")}
                    for t, e in view["tickers"].items()},
        "missing": [], "reason": None,
    }


def frame(view):
    """full_view()'s series as the 1.5.1 (Ticker, Price) download frame."""
    parts = {}
    for t, e in view["tickers"].items():
        index = pd.DatetimeIndex(pd.to_datetime(e["date"]), name="Date")
        parts[t] = pd.DataFrame({"Open": e["open"], "High": e["high"], "Low": e["low"],
                                 "Close": e["close"], "Volume": e["volume"]}, index=index)
    return pd.concat(parts, axis=1, names=["Ticker", "Price"])


class Downloads(list):
    """The download call log; `serve(df)` sets the frame to return. With no
    frame served, a download raises (and the test's `== []` catches it)."""

    frame = None

    def serve(self, df):
        self.frame = df


@pytest.fixture
def downloads(monkeypatch):
    """Patch yf.download; returns the call log."""
    calls = Downloads()

    def fake(tickers, **kwargs):
        calls.append(tickers)
        if calls.frame is None:
            raise AssertionError("download not expected")
        return calls.frame.copy()

    monkeypatch.setattr(quotes.yf, "download", fake)
    return calls


def _assert_real_monitors(health):
    assert list(health["monitors"]) == list(MONITORS)
    for name, result in health["monitors"].items():
        assert set(result) == {"score", "raw", "detail", "stale", "weight"}
        assert result["score"] == FULL_VIEW_SCORES[name], (name, result["detail"])
        assert result["weight"] == WEIGHTS[name]


# ── Weights and the calculator (stubbed monitor results) ─────────

def test_weights_are_part5_and_sum_to_one_hundred():
    assert WEIGHTS == {"vix": 25, "breadth": 20, "spy_trend": 20, "sector_rotation": 15,
                       "volume": 10, "cross_asset": 10}
    assert all(type(w) is int for w in WEIGHTS.values())
    assert sum(WEIGHTS.values()) == 100


def test_health_all_monitors_weighted_rounded():
    h = health_from_monitors(results(vix=60, breadth=60, spy_trend=70, sector_rotation=65,
                                     volume=85, cross_asset=80))       # 67.25
    assert (h["score"], h["regime"], h["coverage"], h["stale"]) == (67, "CAUTIOUS", 100, False)


def test_health_score_rounds_half_up():
    h = health_from_monitors(results(vix=70, breadth=70, spy_trend=70, sector_rotation=70,
                                     volume=65, cross_asset=70))       # exactly 69.5
    assert (h["score"], h["regime"]) == (70, "HEALTHY")


def test_health_renormalizes_over_available_monitors():
    h = health_from_monitors(results(vix=60, breadth=None, spy_trend=70, sector_rotation=65,
                                     volume=85, cross_asset=80))       # 5525 / 80 = 69.06
    assert (h["score"], h["coverage"]) == (69, 80)
    assert h["monitors"]["breadth"]["score"] is None                   # left out, not 0 (would be 55)


def test_health_below_coverage_is_null_not_zero():
    at_floor = health_from_monitors(results(vix=60, breadth=None, spy_trend=70, sector_rotation=65,
                                            volume=None, cross_asset=80))
    assert at_floor["coverage"] == 70 and type(at_floor["score"]) is int
    below = health_from_monitors(results(vix=None, breadth=60, spy_trend=70, sector_rotation=65,
                                         volume=85, cross_asset=None))
    assert (below["score"], below["regime"], below["coverage"]) == (None, None, 65)


def test_health_stale_when_any_contributing_monitor_stale():
    h = health_from_monitors(results(vix=80, breadth=(None, True), spy_trend=95,
                                     sector_rotation=(65, True), volume=85, cross_asset=80))
    assert h["stale"] is True
    assert h["staleMonitors"] == ["sector_rotation"]     # breadth is stale but did not contribute


def test_health_monitor_exception_isolated(monkeypatch, caplog):
    def broken(view):
        raise RuntimeError("bug")

    patched = dict(MONITORS, vix=Monitor(broken, 25, ("^VIX",)))
    monkeypatch.setattr(health_calculator, "MONITORS", patched)
    with caplog.at_level(logging.ERROR, logger="scoring.health_calculator"):
        h = health_from_monitors(health_calculator.run_monitors(full_view()))
    assert h["monitors"]["vix"] == {"score": None, "raw": {}, "detail": "monitor error: RuntimeError",
                                    "stale": False, "weight": 25}
    assert all(h["monitors"][n]["score"] == FULL_VIEW_SCORES[n] for n in MONITORS if n != "vix")
    assert (h["coverage"], h["score"]) == (75, 76)       # 5725 / 75 = 76.33
    assert any("vix" in rec.getMessage() for rec in caplog.records if rec.levelno == logging.ERROR)


# ── Regime classifier ────────────────────────────────────────────

@pytest.mark.parametrize(
    "score, regime",
    [(100, "HEALTHY"), (70, "HEALTHY"), (69, "CAUTIOUS"), (40, "CAUTIOUS"), (39, "DANGER"),
     (20, "DANGER"), (19, "CRITICAL"), (0, "CRITICAL")],
)
def test_regime_boundaries(score, regime):
    assert classify(score) == regime
    assert regime in REGIMES


@pytest.mark.parametrize("bad", [-1, 101, 50.5, True, "70"])
def test_regime_rejects_invalid_score(bad):
    with pytest.raises(ValueError):
        classify(bad)


def test_regime_none_is_none():
    assert classify(None) is None


# ── compute_health: real MONITORS on a real view ─────────────────

@pytest.mark.asyncio
async def test_compute_health_all_fresh(downloads):
    downloads.serve(frame(full_view()))
    h = await compute_health(FakeRedis(), MemoryCooldowns(), now=_now)
    assert len(downloads) == 1
    _assert_real_monitors(h)
    assert (h["score"], h["regime"], h["coverage"], h["stale"]) == (FULL_VIEW_HEALTH, "HEALTHY", 100, False)
    assert h["checkedAt"] == NOW.isoformat()
    assert h["inputs"] == {"asOf": NOW.isoformat(), "source": "fresh", "reason": None, "staleTickers": []}


@pytest.mark.asyncio
async def test_compute_health_from_cached_quotes_no_download(downloads):
    r = FakeRedis()
    r.store[KEY_QUOTES] = json.dumps(envelope(full_view(), NOW))
    h = await compute_health(r, MemoryCooldowns(), now=_now)
    assert downloads == []
    _assert_real_monitors(h)
    assert (h["score"], h["regime"], h["stale"]) == (FULL_VIEW_HEALTH, "HEALTHY", False)
    assert (h["inputs"]["source"], h["inputs"]["reason"]) == ("cached", None)


@pytest.mark.asyncio
async def test_compute_health_cooldown_is_stale_not_error(downloads):
    r = FakeRedis()
    r.store[KEY_LAST] = json.dumps(envelope(full_view(), LAST_AT))
    r.store[COOL_YF] = "1"
    r.ttls[COOL_YF] = 900
    h = await compute_health(r, MemoryCooldowns(), now=_now)
    assert downloads == []
    for name, result in h["monitors"].items():
        assert result["score"] == FULL_VIEW_SCORES[name] and result["stale"] is True, name
    assert (h["score"], h["regime"], h["stale"]) == (FULL_VIEW_HEALTH, "HEALTHY", True)
    assert h["staleMonitors"] == list(MONITORS)
    assert h["inputs"] == {"asOf": LAST_AT.isoformat(), "source": "last_known", "reason": "cooldown",
                           "staleTickers": list(quotes.CORE_TICKERS)}


@pytest.mark.asyncio
async def test_compute_health_output_json_serializable(downloads):
    cached = FakeRedis()
    cached.store[KEY_QUOTES] = json.dumps(envelope(full_view(), NOW))
    blind = FakeRedis()                      # cooling down, nothing to serve: score null
    blind.store[COOL_YF] = "1"
    blind.ttls[COOL_YF] = 900
    for r in (cached, blind):
        h = await compute_health(r, MemoryCooldowns(), now=_now)
        json.dumps(h, allow_nan=False)
    assert (h["score"], h["regime"], h["coverage"], h["inputs"]["source"]) == (None, None, 0, "none")
