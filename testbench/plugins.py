"""Versioned discovery of third-party drivers, steps, and lifecycle hooks."""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol


class StepPlugin(Protocol):
    """Public contract for a custom YAML step action."""

    model: type

    def execute(self, driver: Any, value: Any) -> None: ...


class HookPlugin(Protocol):
    """Optional run lifecycle callbacks exported by a plugin."""

    def before_run(self, result: Any) -> None: ...

    def after_step(self, result: Any, step: Any) -> None: ...

    def on_failure(self, result: Any, step: Any) -> None: ...

    def after_run(self, result: Any) -> None: ...


@lru_cache(maxsize=1)
def _entry_points_by_group() -> dict[str, tuple[EntryPoint, ...]]:
    discovered = entry_points()
    groups = ("testbench.drivers", "testbench.steps", "testbench.hooks")
    return {group: tuple(discovered.select(group=group)) for group in groups}


def driver_entrypoint(name: str) -> EntryPoint | None:
    """Find a registered driver without importing unrelated optional plugins."""
    return next(
        (item for item in _entry_points_by_group()["testbench.drivers"] if item.name == name),
        None,
    )


def step_plugin(name: str) -> StepPlugin | None:
    """Load a step plugin by its YAML action name."""
    selected = next(
        (item for item in _entry_points_by_group()["testbench.steps"] if item.name == name),
        None,
    )
    return selected.load() if selected is not None else None


@lru_cache(maxsize=1)
def hook_plugins() -> tuple[HookPlugin, ...]:
    """Load lifecycle integrations once per process."""
    plugins = []
    for item in _entry_points_by_group()["testbench.hooks"]:
        loaded = item.load()
        plugins.append(loaded() if isinstance(loaded, type) else loaded)
    return tuple(plugins)


def clear_plugin_cache() -> None:
    """Reset discovery caches, primarily for plugin installers and tests."""
    _entry_points_by_group.cache_clear()
    hook_plugins.cache_clear()
