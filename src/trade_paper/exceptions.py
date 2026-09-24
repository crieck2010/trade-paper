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


class MCPError(TradePaperError):
    """Base error for the Robinhood MCP adapter (read-only)."""


class MCPTransportError(MCPError):
    """The MCP server could not be reached or answered unparseably."""


class MCPAuthError(MCPTransportError):
    """Authentication failed (HTTP 401/403) or no token was provided."""


class MCPToolError(MCPError):
    """The MCP server returned a JSON-RPC error, or a tool call failed."""

    def __init__(self, message: str, tool: str | None = None) -> None:
        super().__init__(message)
        self.tool = tool
