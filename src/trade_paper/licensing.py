"""License-key and update-check hooks (stubbed per suite convention)."""

from __future__ import annotations

from . import __version__

UPDATE_URL = "https://api.github.com/repos/crieck2010/trade-paper/releases/latest"


def is_licensed() -> tuple[bool, str]:
    """Paper-trading is free during beta; keyed licensing plugs in here."""
    return False, "beta: no license required"


def check_for_updates() -> dict:
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        latest = str(data.get("tag_name", "")).lstrip("v")
        return {"current": __version__, "latest": latest or "unknown",
                "update_available": bool(latest) and latest != __version__,
                "url": data.get("html_url", "")}
    except Exception as exc:
        return {"current": __version__, "latest": "unknown",
                "update_available": False, "error": str(exc)}
