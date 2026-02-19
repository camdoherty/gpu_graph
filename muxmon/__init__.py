"""muxmon — modular terminal monitor system.

Each monitor is a BaseMonitor subclass that lives in its own module.
Import a monitor module to register it in REGISTRY.
"""

from muxmon.base import BaseMonitor

REGISTRY: dict[str, type[BaseMonitor]] = {}

# Short aliases → canonical name (populated after registration)
ALIASES: dict[str, str] = {
    "mem": "memory",
    "disk": "storage",
    "io": "storage",
}


def register(cls: type[BaseMonitor]) -> type[BaseMonitor]:
    """Decorator that adds a monitor class to the global registry."""
    REGISTRY[cls.name] = cls
    return cls


def resolve(name: str) -> str:
    """Resolve a monitor name, supporting aliases."""
    return ALIASES.get(name, name)
