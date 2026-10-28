"""Compatibility wrapper for the current opt-in AI test generator."""

import yaml

from testbench.ai.config import load_ai_config
from testbench.ai.generate import generate_test


def is_enabled() -> bool:
    """Return whether AI assistance has been explicitly enabled in TOML."""
    return bool(load_ai_config().get("enabled", False))


def text_to_yaml_steps(instruction: str, *, target: str = "web") -> str:
    """Generate validated YAML steps through the configured provider."""
    if not is_enabled():
        raise RuntimeError("AI assistance is disabled. Set [ai].enabled = true in testbench.toml.")
    if not instruction.strip():
        raise ValueError("Instruction must not be empty.")
    if target not in {"web", "android", "game"}:
        raise ValueError(f"Unsupported target: {target}")

    test = generate_test(instruction, target=target)
    return yaml.safe_dump(
        test["steps"],
        sort_keys=False,
        allow_unicode=True,
    )
