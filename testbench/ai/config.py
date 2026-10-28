"""Configuration and environment loading for optional AI features."""

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_AI_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "ollama",
    "model": "llama3.1",
    "vision_model": "llava",
    "max_calls_per_run": 20,
    "redact_secrets": True,
    "healing": "suggest",
    "base_url": None,
    "timeout": 30,
    "retries": 2,
    "consented_providers": [],
    "debug_log": False,
    "max_prompt_chars": 24000,
}


def load_ai_config(path: Path | None = None) -> dict[str, Any]:
    """Load [ai] options from TOML, applying safe defaults."""
    root = Path(__file__).resolve().parents[2]
    config_path = path or Path(os.environ.get("TESTBENCH_CONFIG", root / "testbench.toml"))
    values = dict(DEFAULT_AI_CONFIG)
    if config_path.is_file():
        try:
            with config_path.open("rb") as stream:
                document = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"Could not read AI configuration {config_path}: {exc}") from exc
        section = document.get("ai", {})
        if not isinstance(section, dict):
            raise ValueError("[ai] in testbench.toml must be a table.")
        values.update(section)
    if values.get("provider") not in {
        "ollama",
        "gemini",
        "openai_compatible",
        "openai",
        "groq",
        "openrouter",
        "lm_studio",
        "anthropic",
    }:
        raise ValueError(f"Unknown AI provider {values.get('provider')!r}.")
    if not str(values.get("model", "")).strip():
        raise ValueError("[ai].model must not be empty.")
    if int(values.get("max_calls_per_run", 0)) <= 0:
        raise ValueError("[ai].max_calls_per_run must be a positive integer.")
    if float(values.get("timeout", 0)) <= 0:
        raise ValueError("[ai].timeout must be positive.")
    if int(values.get("retries", -1)) < 0:
        raise ValueError("[ai].retries must be a non-negative integer.")
    if int(values.get("max_prompt_chars", 0)) <= 0:
        raise ValueError("[ai].max_prompt_chars must be a positive integer.")
    if values.get("healing", "suggest") not in {"off", "suggest", "auto"}:
        raise ValueError("[ai].healing must be off, suggest, or auto.")
    if values.get("redact_secrets") is not True:
        raise ValueError("[ai].redact_secrets must be true; private data is always redacted.")
    if not isinstance(values.get("consented_providers", []), list):
        raise ValueError("[ai].consented_providers must be a TOML array.")
    _load_dotenv(config_path.parent / ".env")
    return values


def _load_dotenv(path: Path) -> None:
    """Load key/value pairs only into the process environment; never return them."""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(f"Could not read local .env file {path}: {exc}") from exc
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


def save_provider_consent(provider: str, accepted: bool, path: Path | None = None) -> None:
    """Persist only provider consent in TOML; never persist credentials."""
    known = {"ollama", "gemini", "openai_compatible", "anthropic"}
    if provider not in known:
        raise ValueError(f"Unknown AI provider {provider!r}.")
    config_path = path or Path(
        os.environ.get(
            "TESTBENCH_CONFIG",
            Path(__file__).resolve().parents[2] / "testbench.toml",
        )
    )
    config = load_ai_config(config_path)
    consented = set(config.get("consented_providers", []))
    if accepted:
        consented.add(provider)
    else:
        consented.discard(provider)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    value = "consented_providers = [" + ", ".join(json.dumps(item) for item in sorted(consented)) + "]"
    section = re.search(r"(?ms)^\[ai\]\s*$.*?(?=^\[|\Z)", content)
    if section:
        body = section.group(0)
        if re.search(r"(?m)^consented_providers\s*=", body):
            body = re.sub(r"(?m)^consented_providers\s*=.*$", value, body)
        else:
            body = body.rstrip() + "\n" + value + "\n"
        content = content[: section.start()] + body + content[section.end() :]
    else:
        content = content.rstrip() + ("\n\n" if content.strip() else "") + "[ai]\n" + value + "\n"
    config_path.write_text(content, encoding="utf-8")
