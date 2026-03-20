from __future__ import annotations

from . import github, rss, x

_ADAPTERS = {
    "github_user": github,
    "github_feed": github,
    "rss": rss,
    "web": None,
    "x_home": x,
}

SUPPORTED_SOURCE_KINDS = tuple(_ADAPTERS)


def adapter_for(kind: str):
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        if kind == "web":
            raise KeyError("web sources are handled by agents, not script adapters")
        raise KeyError(kind)
    return adapter
