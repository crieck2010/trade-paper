import json
import pytest

from trade_paper.config import PaperConfig
from trade_paper.exceptions import ConfigError


def test_defaults_validate():
    PaperConfig().validate()


def test_empty_symbols_invalid():
    with pytest.raises(ConfigError):
        PaperConfig(symbols_equities=[], symbols_crypto=[]).validate()


def test_bad_slot_invalid():
    cfg = PaperConfig()
    cfg.schedule.slots = ["25:00"]
    with pytest.raises(ConfigError):
        cfg.validate()


def test_bad_broker_invalid():
    cfg = PaperConfig()
    cfg.broker.name = "nope"
    with pytest.raises(ConfigError):
        cfg.validate()


def test_roundtrip(tmp_path):
    cfg = PaperConfig(name="t", symbols_equities=["SPY"], symbols_crypto=["BTC/USD"])
    cfg.discovery.min_sharpe = 1.5
    p = tmp_path / "c.json"
    cfg.save(p)
    loaded = PaperConfig.load(p)
    assert loaded.name == "t"
    assert loaded.discovery.min_sharpe == 1.5
    assert loaded.all_symbols == ["SPY", "BTC/USD"]


def test_missing_file():
    with pytest.raises(ConfigError):
        PaperConfig.load("/nonexistent/x.json")
