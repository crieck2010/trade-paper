"""Exceptions for trade-paper."""

from __future__ import annotations


class TradePaperError(Exception):
    """Base error for the trade-paper engine."""


class PaperSafetyError(TradePaperError):
    """Raised when anything looks like live (non-paper) trading."""


class BrokerError(TradePaperError):
    """The broker rejected an action or is unreachable."""


class ConfigError(TradePaperError):
    """The paper-trading configuration is invalid."""
