"""Test doubles for sibling suite packages (used ONLY when the real
packages are not installed, so unit tests run hermetically)."""

import sys
import types
from dataclasses import dataclass


def _ensure(modname: str, factory):
    try:
        __import__(modname)
    except ImportError:
        sys.modules[modname] = factory()


def _trade_agents():
    mod = types.ModuleType("trade_agents")

    @dataclass
    class Idea:
        strategy: str
        symbol: str
        direction: str = "long"

    @dataclass
    class Allocation:
        idea: Idea
        quantity: float = 0
        price: float = 0.0

    @dataclass
    class Report:
        allocations: tuple = ()

    class Desk:
        def run(self, provider, equity=100_000.0, **kw):
            allocs = []
            for s in provider.symbols():
                bars = provider.get_bars(s)
                px = bars[-1]["close"] if bars else 100.0
                # one deterministic order per symbol for the "dummy" strategy
                allocs.append(Allocation(Idea(strategy="dummy", symbol=s),
                                        quantity=10, price=px))
            return Report(allocations=tuple(allocs))

    base = types.ModuleType("trade_agents.base")

    class DictBarsProvider:
        def __init__(self, bars):
            self._bars = dict(bars)

        def get_bars(self, symbol):
            return list(self._bars.get(symbol, []))

        def symbols(self):
            return sorted(self._bars)

        def last_price(self, symbol):
            b = self.get_bars(symbol)
            return b[-1]["close"] if b else None

    base.DictBarsProvider = DictBarsProvider
    mod.Desk = Desk
    mod.base = base
    sys.modules["trade_agents.base"] = base
    return mod


def _trade_risk():
    mod = types.ModuleType("trade_risk")

    class RiskManager:
        def __init__(self, *a, **k):
            pass

        def add_limit(self, *a, **k):
            pass

        def check(self, order, state=None, equity=0):
            return True  # stub approves everything; real limits tested in trade-risk

    mod.RiskManager = RiskManager
    return mod


_ensure("trade_agents", _trade_agents)
_ensure("trade_risk", _trade_risk)
