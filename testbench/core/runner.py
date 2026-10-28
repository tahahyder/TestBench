"""Load and execute TestBench YAML test definitions."""

import importlib
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from testbench.ai.redaction import redact_text
from testbench.core.steps import BUILTIN_ACTIONS, Step, TestSpec
from testbench.drivers.base import Driver


@dataclass
class StepResult:
    """Outcome and timing for one test step."""

    index: int
    action: str
    status: str
    duration_seconds: float
    error: str | None = None
    screenshot: str | None = None
    old_locator: Any = None
    new_locator: Any = None
    healing_method: str | None = None
    healing_confidence: float | None = None


@dataclass
class RunResult:
    """Serializable outcome of a complete test run."""

    run_id: str
    test_name: str
    target: str
    status: str
    started_at: str
    duration_seconds: float
    steps: list[StepResult] = field(default_factory=list)
    artifact_dir: str = ""
    report_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ProgressCallback = Callable[[RunResult], None]


def load_test(path: Path) -> TestSpec:
    """Read YAML from disk and validate its test specification."""
    try:
        with path.open(encoding="utf-8") as test_file:
            raw: Any = yaml.safe_load(test_file)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Test file {path} must contain a YAML mapping.")
    try:
        return TestSpec.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid test definition in {path}:\n{exc}") from exc


def run_test(
    test_path: Path,
    *,
    runs_dir: Path = Path("runs"),
    reports_dir: Path = Path("reports"),
    database_path: Path | None = None,
    driver: Driver | None = None,
    run_id: str | None = None,
    on_progress: ProgressCallback | None = None,
    ai_generated: bool = False,
    ai_run_id: str | None = None,
) -> RunResult:
    """Execute a validated test and capture a screenshot on a failed step."""
    spec = load_test(test_path)
    from testbench.ai.config import load_ai_config

    ai_settings = load_ai_config()
    healing_mode = str(spec.config.get("healing", ai_settings.get("healing", "suggest")))
    if healing_mode not in {"off", "suggest", "auto"}:
        raise ValueError("config.healing must be off, suggest, or auto.")
    _resolve_test_variables(spec)
    current_run_id = run_id or uuid.uuid4().hex
    artifact_dir = runs_dir / current_run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    selected_driver = driver or _create_driver(spec.target, spec.config, artifact_dir)

    started = datetime.now(UTC)
    start_clock = time.monotonic()
    generated_marker = False
    try:
        generated_marker = "# TestBench-AI: generated" in test_path.read_text(encoding="utf-8")
    except OSError:
        generated_marker = False
    run_metadata: dict[str, Any] = {"source_test": str(test_path.resolve())}
    if ai_generated or generated_marker:
        run_metadata["ai_features"] = {"generated_steps": list(range(1, len(spec.steps) + 1))}
    result = RunResult(
        run_id=current_run_id,
        test_name=spec.name,
        target=spec.target,
        status="running",
        started_at=started.isoformat(),
        duration_seconds=0.0,
        artifact_dir=str(artifact_dir.resolve()),
        metadata=run_metadata,
    )
    _write_run_log(
        artifact_dir / "run.log",
        "run_started",
        run_id=current_run_id,
        test_name=redact_text(spec.name)[0],
        target=spec.target,
    )
    _run_hooks(result, "before_run")
    _notify(on_progress, result)

    try:
        for index, step in enumerate(spec.steps, start=1):
            step_started = time.monotonic()
            try:
                screenshot = _execute_step(selected_driver, step, artifact_dir, index)
                step_result = StepResult(
                    index,
                    step.action,
                    "passed",
                    time.monotonic() - step_started,
                    screenshot=screenshot,
                )
            except Exception as exc:
                mode = str(healing_mode)
                suggestion = None
                assertion = step.action.startswith("expect_")
                if mode != "off" and (not assertion or mode == "suggest"):
                    from testbench.core.database import RunStore
                    from testbench.core.healing import (
                        is_locator_failure,
                        non_ai_suggestions,
                        replace_step_locator,
                    )

                    if is_locator_failure(exc):
                        candidates = non_ai_suggestions(selected_driver, step)
                        if not candidates:
                            try:
                                if ai_settings.get("enabled", False):
                                    from testbench.ai.client import AIClient
                                    from testbench.ai.healing import suggest_locator

                                    ai_client = AIClient(
                                        ai_settings,
                                        database_path=database_path or runs_dir / "testbench.sqlite3",
                                    )
                                    ai_candidate = suggest_locator(
                                        selected_driver,
                                        step,
                                        client=ai_client,
                                        run_id=current_run_id,
                                    )
                                    if ai_candidate is not None:
                                        candidates = [ai_candidate]
                            except (OSError, RuntimeError, ValueError) as ai_error:
                                result.metadata.setdefault("ai_errors", []).append(
                                    f"Step {index} AI healing was unavailable: {ai_error}"
                                )
                        if candidates:
                            suggestion = candidates[0]
                            suggestion_data = suggestion.to_dict()
                            record_id = RunStore(database_path or runs_dir / "testbench.sqlite3").record_healing(
                                current_run_id,
                                index,
                                suggestion_data,
                                accepted=True if mode == "auto" and not assertion else None,
                            )
                            suggestion_data.update(
                                {
                                    "id": record_id,
                                    "step_index": index,
                                    "action": step.action,
                                }
                            )
                            result.metadata.setdefault("healing_suggestions", []).append(suggestion_data)
                            if mode == "auto" and not assertion:
                                healed_step = replace_step_locator(step, suggestion.new_locator)
                                try:
                                    screenshot = _execute_step(selected_driver, healed_step, artifact_dir, index)
                                except Exception:
                                    pass
                                else:
                                    step_result = StepResult(
                                        index=index,
                                        action=step.action,
                                        status="healed",
                                        duration_seconds=time.monotonic() - step_started,
                                        screenshot=screenshot,
                                        old_locator=suggestion.old_locator,
                                        new_locator=suggestion.new_locator,
                                        healing_method=suggestion.method,
                                        healing_confidence=suggestion.confidence,
                                    )
                                    result.steps.append(step_result)
                                    _write_run_log(
                                        artifact_dir / "run.log",
                                        "step_completed",
                                        index=index,
                                        action=step.action,
                                        status="healed",
                                        duration_seconds=step_result.duration_seconds,
                                    )
                                    result.metadata.setdefault("healed_steps", []).append(suggestion_data)
                                    _run_hooks(result, "after_step", step_result)
                                    _notify(on_progress, result)
                                    continue
                failure_screenshot = artifact_dir / f"failure_step_{index:03d}.png"
                screenshot_path: str | None = None
                try:
                    selected_driver.screenshot(failure_screenshot)
                    screenshot_path = str(failure_screenshot.resolve())
                except Exception as screenshot_error:
                    exc_message = (
                        f"{type(exc).__name__}: {exc}; failure screenshot could not "
                        f"be saved: {type(screenshot_error).__name__}: {screenshot_error}"
                    )
                else:
                    exc_message = f"{type(exc).__name__}: {exc}"
                step_result = StepResult(
                    index=index,
                    action=step.action,
                    status="failed",
                    duration_seconds=time.monotonic() - step_started,
                    error=exc_message,
                    screenshot=screenshot_path,
                    old_locator=suggestion.old_locator if suggestion else None,
                    new_locator=suggestion.new_locator if suggestion else None,
                    healing_method=suggestion.method if suggestion else None,
                    healing_confidence=suggestion.confidence if suggestion else None,
                )
                result.steps.append(step_result)
                result.status = "failed"
                _write_run_log(
                    artifact_dir / "run.log",
                    "step_completed",
                    index=index,
                    action=step.action,
                    status="failed",
                    duration_seconds=step_result.duration_seconds,
                    error_type=type(exc).__name__,
                )
                _run_hooks(result, "after_step", step_result)
                _run_hooks(result, "on_failure", step_result)
                for skipped_index, skipped_step in enumerate(spec.steps[index:], start=index + 1):
                    result.steps.append(
                        StepResult(
                            index=skipped_index,
                            action=skipped_step.action,
                            status="skipped",
                            duration_seconds=0.0,
                            error="Skipped because an earlier step failed.",
                        )
                    )
                _notify(on_progress, result)
                break

            result.steps.append(step_result)
            _write_run_log(
                artifact_dir / "run.log",
                "step_completed",
                index=index,
                action=step.action,
                status=step_result.status,
                duration_seconds=step_result.duration_seconds,
            )
            _run_hooks(result, "after_step", step_result)
            _notify(on_progress, result)
    finally:
        try:
            selected_driver.close()
        except Exception as exc:
            if result.status != "failed":
                result.status = "failed"
            result.steps.append(
                StepResult(
                    index=len(result.steps) + 1,
                    action="close",
                    status="failed",
                    duration_seconds=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            _write_run_log(
                artifact_dir / "run.log",
                "driver_close_failed",
                error_type=type(exc).__name__,
            )

    result.metadata = {
        **dict(getattr(selected_driver, "metadata", {})),
        **result.metadata,
    }
    try:
        from testbench.ai.client import AIClient

        if ai_settings.get("enabled", False) or ai_run_id:
            ai_client = AIClient(
                ai_settings,
                database_path=database_path or runs_dir / "testbench.sqlite3",
            )
            usage_rows = [
                ai_client.usage(identifier) for identifier in dict.fromkeys((current_run_id, ai_run_id)) if identifier
            ]
            result.metadata["ai_usage"] = {
                key: sum(row[key] for row in usage_rows) for key in ("calls", "estimated_tokens", "cache_hits")
            }
            result.metadata["ai_provider"] = ai_settings.get("provider")
            result.metadata["ai_model"] = ai_settings.get("model")
    except (OSError, RuntimeError, ValueError) as ai_error:
        result.metadata.setdefault("ai_errors", []).append(f"Could not read AI usage: {ai_error}")
    if result.status == "running":
        result.status = "passed"
    if result.status == "failed":
        from testbench.ai.explain import analyze_failure

        failure = next((step for step in result.steps if step.status == "failed"), None)
        if failure is not None:
            result.metadata["failure_explanation"] = analyze_failure(
                failure.error or "",
                target=spec.target,
                diagnostics=dict(getattr(selected_driver, "metadata", {})),
            )
    result.duration_seconds = time.monotonic() - start_clock
    _run_hooks(result, "after_run")
    from testbench.core.reporter import generate_report

    result.report_path = str(generate_report(result, reports_dir).resolve())
    from testbench.core.database import RunStore

    RunStore(database_path or runs_dir / "testbench.sqlite3").save(result)
    _write_run_log(
        artifact_dir / "run.log",
        "run_completed",
        run_id=result.run_id,
        status=result.status,
        duration_seconds=result.duration_seconds,
    )
    _notify(on_progress, result)
    return result


def _create_driver(target: str, config: dict[str, Any], artifact_dir: Path) -> Driver:
    from testbench.plugins import driver_entrypoint

    entrypoint = driver_entrypoint(target)
    if entrypoint is not None:
        driver_type = entrypoint.load()
        return driver_type(config=config, artifact_dir=artifact_dir)
    module_name = "mobile" if target == "android" else target
    class_name = "".join(part.title() for part in module_name.split("_")) + "Driver"
    try:
        module = importlib.import_module(f"testbench.drivers.{module_name}")
    except ModuleNotFoundError as exc:
        extra = {"mobile": "mobile", "game": "game"}.get(module_name)
        if extra is None:
            raise
        raise RuntimeError(
            f'The {target} driver needs optional dependencies. Install them with: pip install "testbench[{extra}]".'
        ) from exc
    driver_type = getattr(module, class_name)
    return driver_type(config=config, artifact_dir=artifact_dir)


def _execute_step(driver: Driver, step: Step, artifact_dir: Path, index: int) -> str | None:
    from testbench.plugins import step_plugin

    if step.action not in BUILTIN_ACTIONS:
        plugin = step_plugin(step.action)
        if plugin is None:
            raise ValueError(f"Unsupported step action: {step.action}")
        if isinstance(plugin, type):
            plugin = plugin()
        plugin.execute(driver, step.value)
    elif step.action == "open":
        driver.open(str(step.value))
    elif step.action == "click":
        driver.click(step.value)
    elif step.action == "type":
        assert isinstance(step.value, dict)
        driver.type(step.value["field"], step.value["value"])
    elif step.action == "select_option":
        assert isinstance(step.value, dict)
        _call_driver_method(
            driver,
            "select_option",
            step.value["field"],
            step.value["value"],
        )
    elif step.action == "scroll":
        _call_driver_method(driver, "scroll", step.value)
    elif step.action == "expect_url_contains":
        _call_driver_method(driver, "expect_url_contains", str(step.value))
    elif step.action == "expect_enabled":
        _call_driver_method(
            driver,
            "expect_enabled",
            step.value["target"],
            step.value["enabled"],
        )
    elif step.action == "wait":
        driver.wait(float(step.value))
    elif step.action == "expect_visible":
        driver.expect_visible(step.value)
    elif step.action == "expect_text":
        driver.expect_text(str(step.value))
    elif step.action == "screenshot":
        label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(step.value)).strip("_")
        path = artifact_dir / "screenshots" / f"{index:03d}_{label or 'screenshot'}.png"
        driver.screenshot(path)
        return str(path.resolve())
    elif step.action == "swipe":
        assert isinstance(step.value, dict)
        driver.swipe(step.value["direction"])
    elif step.action == "scroll_to":
        driver.scroll_to(str(step.value))
    elif step.action == "back":
        driver.back()
    elif step.action == "home":
        driver.home()
    elif step.action == "hide_keyboard":
        driver.hide_keyboard()
    elif step.action == "press_key":
        driver.press_key(str(step.value))
    elif step.action == "set_orientation":
        driver.set_orientation(str(step.value))
    elif step.action in {"wait_for_image", "click_image", "expect_image", "expect_no_image"}:
        _call_driver_method(driver, step.action, step.value)
    elif step.action == "click_text":
        _call_driver_method(driver, "click_text", str(step.value))
    elif step.action == "hold_key":
        _call_driver_method(
            driver,
            "hold_key",
            str(step.value["key"]),
            float(step.value["seconds"]),
        )
    elif step.action in {"tap", "drag", "wait_for_change", "expect_no_change"}:
        _call_driver_method(driver, step.action, step.value)
    elif step.action == "measure_fps":
        assert isinstance(step.value, dict)
        _call_driver_method(
            driver,
            "measure_fps",
            float(step.value["seconds"]),
            float(step.value["min_fps"]),
        )
    else:
        raise ValueError(f"Unsupported step action: {step.action}")
    return None


def _call_driver_method(driver: Driver, name: str, *args: Any) -> Any:
    method = getattr(driver, name, None)
    if not callable(method):
        raise NotImplementedError(f"{type(driver).__name__} does not support the {name} step.")
    return method(*args)


def _resolve_test_variables(spec: TestSpec) -> None:
    """Resolve declared ${NAME} placeholders from the local process environment."""
    variables = {}
    missing = []
    for name in spec.variables:
        value = os.environ.get(name)
        if value is None:
            missing.append(name)
        else:
            variables[name] = value
    if missing:
        raise ValueError("Missing required environment variables for this test: " + ", ".join(missing))

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            for name, actual in variables.items():
                value = value.replace("${" + name + "}", actual)
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    for step in spec.steps:
        step.value = replace(step.value)


def _notify(callback: ProgressCallback | None, result: RunResult) -> None:
    if callback is not None:
        callback(result)


class _RunJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "testbench_fields", {}),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _write_run_log(path: Path, event: str, **fields: Any) -> None:
    """Append one structured, value-free event to the run-local log."""
    logger = logging.getLogger(f"testbench.run.{path.parent.name}")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(_RunJSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info(event, extra={"testbench_fields": fields})
    finally:
        logger.removeHandler(handler)
        handler.close()


def _run_hooks(result: RunResult, hook_name: str, step: StepResult | None = None) -> None:
    """Run optional hooks and expose integration failures in run metadata."""
    from testbench.plugins import hook_plugins

    try:
        plugins = hook_plugins()
    except Exception as exc:
        result.metadata.setdefault("plugin_errors", []).append(
            f"Could not load plugins for {hook_name}: {type(exc).__name__}: {exc}"
        )
        return
    for plugin in plugins:
        callback = getattr(plugin, hook_name, None)
        if not callable(callback):
            continue
        try:
            if hook_name == "after_step" or hook_name == "on_failure":
                callback(result, step)
            else:
                callback(result)
        except Exception as exc:
            result.metadata.setdefault("plugin_errors", []).append(
                f"{type(plugin).__name__}.{hook_name} failed: {type(exc).__name__}: {exc}"
            )
