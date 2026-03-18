"""
Central config loader. All modules import `config` from here.
Config is loaded once at startup from config.yaml (path resolved relative to project root).
"""
import os
import yaml
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(os.environ.get("GOALFORGE_CONFIG", Path(__file__).parent.parent / "config.yaml"))

_raw: dict = {}


def _load() -> dict:
    with open(_CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def reload():
    global _raw
    _raw = _load()


def get_raw() -> dict:
    return _raw


def get(key_path: str, default: Any = None) -> Any:
    """Dot-separated key lookup, e.g. get('llm.provider')"""
    parts = key_path.split(".")
    node = _raw
    for p in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(p)
        if node is None:
            return default
    return node


class _AttrDict:
    """Wraps a dict for attribute-style access (config.llm.provider)."""
    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        val = self._data.get(item)
        if isinstance(val, dict):
            return _AttrDict(val)
        return val

    def __getitem__(self, item):
        val = self._data[item]
        if isinstance(val, dict):
            return _AttrDict(val)
        return val

    def get(self, item, default=None):
        val = self._data.get(item, default)
        if isinstance(val, dict):
            return _AttrDict(val)
        return val

    def __repr__(self):
        return repr(self._data)

    def to_dict(self):
        return self._data


class _ConfigProxy:
    """
    Top-level proxy so `from goalforge.config import config` gives attribute access.
    Always reads from _raw so hot-reload works.
    """
    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        val = _raw.get(item)
        if isinstance(val, dict):
            return _AttrDict(val)
        return val

    def __getitem__(self, item):
        val = _raw[item]
        if isinstance(val, dict):
            return _AttrDict(val)
        return val


config = _ConfigProxy()

# Load on import
reload()
