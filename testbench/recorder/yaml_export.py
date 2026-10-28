"""Stable serialization of recorded steps to the TestBench YAML format."""

from typing import Any

import yaml

from testbench.core.steps import Step


def test_document(
    *,
    name: str,
    target: str,
    config: dict[str, Any],
    steps: list[Step],
    variables: list[str] | None = None,
) -> dict[str, Any]:
    """Build a predictable YAML-ready test document."""
    document: dict[str, Any] = {
        "name": name,
        "target": target,
        "config": config,
    }
    if variables:
        document["variables"] = sorted(set(variables))
    document["steps"] = [{step.action: step.value} for step in steps]
    return document


def dump_test_yaml(**kwargs: Any) -> str:
    """Serialize a recorder test with stable top-level and mapping key order."""
    return yaml.safe_dump(
        test_document(**kwargs),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
