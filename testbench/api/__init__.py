"""Stable public extension API (compatibility level 1.x)."""

from testbench.core.steps import Step, TestSpec
from testbench.drivers.base import Driver
from testbench.plugins import HookPlugin, StepPlugin

API_VERSION = "1.0"

__all__ = [
    "API_VERSION",
    "Driver",
    "HookPlugin",
    "Step",
    "StepPlugin",
    "TestSpec",
]
