import pytest

from trade_paper import discovery as dmod
from trade_paper.datafeed import asset_class_of, fetch_bars, normalize_symbol, synth_bars


def test_asset_class():
    assert asset_class_of("AAPL") == "equity"
    assert asset_class_of("BTC/USD") == "crypto"
    assert asset_class_of("ETH-USD") == "crypto"


def test_normalize_symbol():
    assert normalize_symbol("btc-usd") == "BTC/USD"
    assert normalize_symbol("aapl") == "AAPL"


def test_synth_bars_shape():
    bars = synth_bars("X", n=50)
    assert len(bars) == 50 and bars[0]["close"] > 0


def test_fetch_demo():
    out = fetch_bars(["AAPL", "BTC/USD"], source="demo", days=100)
    assert set(out) == {"AAPL", "BTC/USD"}
    assert len(out["AAPL"]) == 100


def test_pearson():
    assert dmod.pearson([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert dmod.pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert dmod.pearson([1, 1, 1], [1, 2, 3]) == 0.0


def test_daily_returns():
    bars = [{"close": 100}, {"close": 110}, {"close": 99}]
    r = dmod.daily_returns(bars)
    assert r[0] == pytest.approx(0.10) and r[1] == pytest.approx(-0.10)


def test_max_pairwise_correlation():
    import math
    import random
    rng = random.Random(42)

    def series(base, sign):
        px, out = base, []
        for i in range(120):
            # varying daily return: shared wave + idiosyncratic noise
            r = sign * (0.02 * math.sin(i / 6.0)) + rng.uniform(-0.002, 0.002)
            px *= (1 + r)
            out.append({"close": px})
        return out

    up = series(100.0, +1.0)    # returns track +wave
    down = series(500.0, -1.0)  # returns track -wave -> strongly correlated
    assert dmod.max_pairwise_correlation({"A": up, "B": down}) > 0.9
    # identical series correlate ~1
    assert dmod.max_pairwise_correlation({"A": up, "B": [dict(b) for b in up]}) > 0.99
    # independent noise does not correlate
    flat1 = [{"close": 100 + rng.uniform(-1, 1)} for _ in range(120)]
    flat2 = [{"close": 100 + rng.uniform(-1, 1)} for _ in range(120)]
    assert dmod.max_pairwise_correlation({"A": flat1, "B": flat2}) < 0.4


def test_composite_score_rewards_sharpe_punishes_dd():
    good = {"sharpe_ratio": 2.0, "max_drawdown": 0.05, "profit_factor": 2.0,
            "num_trades": 40, "total_return": 0.3}
    bad = {"sharpe_ratio": 0.5, "max_drawdown": 0.4, "profit_factor": 1.1,
           "num_trades": 40, "total_return": 0.05}
    assert dmod.composite_score(good) > dmod.composite_score(bad)


def test_composite_score_needs_trades():
    m = {"sharpe_ratio": 5.0, "max_drawdown": 0.01, "profit_factor": 5.0,
         "num_trades": 2, "total_return": 0.5}
    assert dmod.composite_score(m) < 1.0  # low confidence
