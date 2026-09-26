"""trade-paper: paper-trading execution engine for the trade-suite.

Pipeline
--------
``trade-agents`` Desk -> ``trade-risk`` review -> ``trade-paper`` broker.

The engine is broker-agnostic (``Broker`` ABC).  The only live broker is
Alpaca's *paper* endpoint -- the engine refuses to touch a live URL, so it
cannot trade real money even by misconfiguration.

Paper-only.  Research and education -- not investment advice.
"""

from __future__ import annotations

__version__ = "0.3.0"

from .config import PaperConfig
from .exceptions import (
    BrokerError,
    ConfigError,
    MCPAuthError,
    MCPError,
    MCPToolError,
    MCPTransportError,
    PaperSafetyError,
    TradePaperError,
)

__all__ = [
    "__version__",
    "PaperConfig",
    "BrokerError",
    "ConfigError",
    "MCPAuthError",
    "MCPError",
    "MCPToolError",
    "MCPTransportError",
    "PaperSafetyError",
    "TradePaperError",
]
