from __future__ import annotations

from . import github, rss

_ADAPTERS = {
    "github_user": github,
    "github_feed": github,
    "rss": rss,
    "web": None,
}

SUPPORTED_SOURCE_KINDS = tuple(_ADAPTERS)


def adapter_for(kind: str):
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        if kind == "web":
            raise KeyError("web sources are handled by platform-native web collectors, not platform adapters")
        raise KeyError(kind)
    return adapter
