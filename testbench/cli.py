"""TestBench command-line interface."""

import glob
import json
import logging
import os
import re
import subprocess
import sys
import traceback
import uuid
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer
import yaml

app = typer.Typer(help="Local-first test automation workbench.")
mobile_app = typer.Typer(help="Manage the local Appium server.")
record_app = typer.Typer(help="Record interactions into a YAML test.")
ai_app = typer.Typer(help="Optional AI test-writing and analysis helpers.")
heal_app = typer.Typer(help="Manage accepted locator healing fixes.")
app.add_typer(mobile_app, name="mobile")
app.add_typer(record_app, name="record")
app.add_typer(ai_app, name="ai")
app.add_typer(heal_app, name="heal")


class _CLIJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )


@app.callback()
def app_options(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks for errors."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logs."),
    quiet: bool = typer.Option(False, "--quiet", help="Only show warnings and errors."),
    log_json: bool = typer.Option(False, "--log-json", help="Format logs as JSON."),
) -> None:
    """Configure command-wide diagnostics and logging."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug
    level = logging.WARNING if quiet else logging.DEBUG if verbose else logging.INFO
    if log_json:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_CLIJSONFormatter())
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        logging.basicConfig(
            level=level,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
            force=True,
        )


@app.command()
def init(
    project_dir: Path = typer.Argument(Path("."), help="Directory to initialize."),
) -> None:
    """Create a starter TestBench project without overwriting existing files."""
    from testbench.setup.project import initialize_project

    try:
        created = initialize_project(project_dir)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not initialize project: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Initialized TestBench project in {project_dir.resolve()}")
    for path in created:
        typer.echo(f"  Created {path}")
    typer.echo("Next: testbench doctor")


@app.command()
def run(
    test_file: str,
    ctx: typer.Context,
    workers: int = typer.Option(1, min=1, help="Parallel web-test workers."),
    retries: int = typer.Option(0, min=0, help="Retry failed tests this many times."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Run tests with this tag (repeatable)."),
    junit: Path | None = typer.Option(None, help="Write JUnit XML results."),
    json_results: Path | None = typer.Option(None, "--json-results", help="Write JSON results."),
) -> None:
    """Run one YAML file, a folder, or a glob pattern."""
    from testbench.core.database import RunStore
    from testbench.core.reporter import generate_report
    from testbench.core.runner import RunResult, load_test, run_test

    files = _find_test_files(test_file)
    if not files:
        typer.echo(f"No YAML tests found for {test_file!r}.", err=True)
        raise typer.Exit(code=2)
    try:
        selected = []
        wanted_tags = set(tag or [])
        for path in files:
            spec = load_test(path)
            if wanted_tags and not wanted_tags.issubset(set(spec.tags)):
                continue
            selected.append((path, spec.target))
    except (OSError, ValueError) as exc:
        if ctx.obj.get("debug"):
            traceback.print_exc()
        typer.echo(f"Test setup error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not selected:
        typer.echo("No tests matched the requested tags.", err=True)
        raise typer.Exit(code=2)

    def execute(path: Path) -> RunResult:
        attempts = []
        result = run_test(path)
        attempts.append(result)
        for _ in range(retries):
            if result.status == "passed":
                break
            result = run_test(path)
            attempts.append(result)
        if len(attempts) > 1 and result.status == "passed":
            result.metadata["flaky"] = True
            result.metadata["retry_run_ids"] = [attempt.run_id for attempt in attempts[:-1]]
            result.report_path = str(generate_report(result).resolve())
            RunStore(Path("runs") / "testbench.sqlite3").save(result)
        return result

    results_by_path: dict[Path, RunResult] = {}
    try:
        web_paths = [path for path, target in selected if target == "web"]
        serial_paths = [path for path, target in selected if target != "web"]
        if workers > 1 and web_paths:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(execute, path): path for path in web_paths}
                for future in as_completed(futures):
                    results_by_path[futures[future]] = future.result()
        else:
            for path in web_paths:
                results_by_path[path] = execute(path)
        for path in serial_paths:
            results_by_path[path] = execute(path)
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        if ctx.obj.get("debug"):
            traceback.print_exc()
        typer.echo(f"Test setup error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    results = [results_by_path[path] for path, _ in selected]
    for result in results:
        state = "FLAKY" if result.metadata.get("flaky") else result.status.upper()
        typer.echo(f"{state}: {result.test_name} ({result.duration_seconds:.2f}s) - run {result.run_id}")
        if result.report_path:
            typer.echo(f"Report: {result.report_path}")
    try:
        if junit:
            _write_junit(results, junit)
        if json_results:
            json_results.parent.mkdir(parents=True, exist_ok=True)
            json_results.write_text(
                json.dumps([result.to_dict() for result in results], indent=2),
                encoding="utf-8",
            )
    except OSError as exc:
        typer.echo(f"Could not write result output: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if any(result.status != "passed" for result in results):
        raise typer.Exit(code=1)


def _find_test_files(pattern: str) -> list[Path]:
    """Expand a file, directory, or glob into sorted YAML files."""
    if any(character in pattern for character in "*?["):
        candidates = [Path(item) for item in glob.glob(pattern, recursive=True)]
    else:
        candidate = Path(pattern).expanduser()
        candidates = (
            list(candidate.rglob("*.yaml")) + list(candidate.rglob("*.yml")) if candidate.is_dir() else [candidate]
        )
    return sorted(
        {path.resolve() for path in candidates if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}},
        key=lambda item: str(item).casefold(),
    )


def _write_junit(results, destination: Path) -> None:
    """Write standards-compatible JUnit XML for CI systems."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    failures = sum(result.status != "passed" for result in results)
    suite = ET.Element(
        "testsuite",
        {
            "name": "TestBench",
            "tests": str(len(results)),
            "failures": str(failures),
            "time": f"{sum(result.duration_seconds for result in results):.3f}",
        },
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": result.target,
                "name": result.test_name,
                "time": f"{result.duration_seconds:.3f}",
            },
        )
        if result.status != "passed":
            failure = ET.SubElement(
                case,
                "failure",
                {"message": f"{result.status}: {result.test_name}"},
            )
            failure.text = "\n".join(
                step.error or f"{step.action} failed" for step in result.steps if step.status == "failed"
            )
        if result.metadata.get("flaky"):
            properties = ET.SubElement(case, "properties")
            ET.SubElement(
                properties,
                "property",
                {"name": "flaky", "value": "true"},
            )
    ET.indent(suite, space="  ")
    ET.ElementTree(suite).write(destination, encoding="utf-8", xml_declaration=True)


@app.command()
def doctor(
    mobile: bool = typer.Option(False, "--mobile", help="Check all Android prerequisites."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Check local automation dependencies."""
    from testbench.setup.doctor import run_doctor

    raise typer.Exit(code=run_doctor(mobile=mobile, json_output=json_output))


@app.command()
def devices() -> None:
    """List connected Android devices and available AVDs."""
    from testbench.drivers.devices import DeviceManager

    manager = DeviceManager()
    errors = []
    try:
        connected = manager.list_devices()
    except (OSError, RuntimeError) as exc:
        connected = []
        errors.append(str(exc))
    typer.echo("Connected devices:")
    if not connected:
        typer.echo("  (none)" if not errors else f"  unavailable: {errors[-1]}")
    for device in connected:
        typer.echo(f"  {device['serial']}  {device['model']}  Android {device['android_version']}  ({device['type']})")
    try:
        avds = manager.list_avds()
    except (OSError, RuntimeError) as exc:
        avds = []
        errors.append(str(exc))
    typer.echo("Available AVDs:")
    for avd in avds or (["(none)"] if not errors else [f"unavailable: {errors[-1]}"]):
        typer.echo(f"  {avd}")
    if errors:
        raise typer.Exit(code=1)


@mobile_app.command("install")
def mobile_install() -> None:
    """Install Appium 2 and its UiAutomator2 driver with npm."""
    from testbench.setup.mobile_setup import install_appium

    try:
        install_appium()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        typer.echo(f"Appium setup failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Appium 2 and UiAutomator2 are installed.")


@mobile_app.command("start")
def mobile_start(
    host: str = typer.Option("127.0.0.1", help="Appium bind address."),
    port: int = typer.Option(4723, help="Appium port."),
) -> None:
    """Start Appium in the background and wait for its health check."""
    from testbench.setup.mobile_setup import start_appium

    try:
        pid = start_appium(host=host, port=port)
    except (OSError, RuntimeError, TimeoutError) as exc:
        typer.echo(f"Could not start Appium: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Appium is healthy at http://{host}:{port} (PID {pid}).")


@mobile_app.command("stop")
def mobile_stop() -> None:
    """Stop the Appium server started by TestBench."""
    from testbench.setup.mobile_setup import stop_appium

    try:
        stopped = stop_appium()
    except (OSError, RuntimeError) as exc:
        typer.echo(f"Could not stop Appium: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Appium stopped." if stopped else "No managed Appium server is running.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, help="Port to bind."),
    token: str | None = typer.Option(None, help="Required access token when binding publicly."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the dashboard."),
) -> None:
    """Start the local TestBench dashboard and API."""
    import webbrowser
    from threading import Timer

    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"}:
        if not token:
            typer.echo(
                "Binding beyond localhost requires --token to protect the dashboard.",
                err=True,
            )
            raise typer.Exit(code=2)
        os.environ["TESTBENCH_AUTH_TOKEN"] = token
        typer.echo(
            "Warning: the dashboard is network-accessible. Share the token only "
            "with trusted users and restrict network access."
        )
    elif token:
        os.environ["TESTBENCH_AUTH_TOKEN"] = token
    dashboard_url = f"http://127.0.0.1:{port}/" if host == "0.0.0.0" else f"http://{host}:{port}/"
    if not no_browser:
        Timer(1.0, webbrowser.open, args=(dashboard_url,)).start()
    uvicorn.run("testbench.api.main:app", host=host, port=port)


@record_app.command("web")
def record_web(url: str) -> None:
    """Record a headed browser session at URL."""
    _record_cli("web", {"url": url, "headless": False})


@record_app.command("android")
def record_android(
    device: str = typer.Option("auto", help="adb device serial or auto."),
    package: str = typer.Option(..., help="Installed Android package name."),
    app: str | None = typer.Option(None, help="Optional APK path."),
    appium_url: str = typer.Option("http://127.0.0.1:4723"),
) -> None:
    """Record interaction on an Android emulator or connected device."""
    config = {
        "device": device,
        "package": package,
        "appium_url": appium_url,
        "reset": False,
    }
    if app:
        config["app"] = app
    _record_cli("android", config)


@record_app.command("game")
def record_game(
    surface: str = typer.Option("desktop", help="desktop, android, or browser."),
    window: str | None = typer.Option(None, help="Desktop game window title."),
    device: str = typer.Option("auto", help="Android device serial."),
    url: str | None = typer.Option(None, help="Browser game URL."),
    templates_dir: Path = typer.Option(Path("templates")),
) -> None:
    """Record game clicks as image-template steps."""
    config = {
        "surface": surface,
        "window_title": window,
        "device": device,
        "url": url,
        "templates_dir": str(templates_dir),
    }
    _record_cli("game", config)


@app.command()
def export(
    test_file: Path,
    format: str = typer.Option("pytest-playwright", "--format", help="Export format."),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Export a web YAML test to readable pytest-playwright code."""
    if format != "pytest-playwright":
        typer.echo("Supported export format: pytest-playwright.", err=True)
        raise typer.Exit(code=2)
    from testbench.recorder.export import write_pytest_playwright

    try:
        path = write_pytest_playwright(test_file, output)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not export test: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Exported pytest-playwright test: {path}")


@app.command()
def share(
    run_id: str,
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Bundle a standalone HTML report and its local media into a ZIP."""
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        typer.echo("Run ID must be a 32-character hexadecimal ID.", err=True)
        raise typer.Exit(code=2)
    report_dir = Path("reports") / run_id
    if not (report_dir / "index.html").is_file():
        typer.echo(f"No report found for run {run_id}.", err=True)
        raise typer.Exit(code=2)
    destination = output or (Path("reports") / f"{run_id}.zip")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(report_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(report_dir.parent))
    except OSError as exc:
        typer.echo(f"Could not bundle report: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Run bundle: {destination.resolve()}")


@ai_app.command("doctor")
def ai_doctor() -> None:
    """Check selected AI provider connectivity and model availability."""
    from testbench.ai.config import load_ai_config
    from testbench.ai.providers import create_provider

    config = load_ai_config()
    provider = create_provider(config)
    model = str(config.get("model", ""))
    try:
        healthy, message = provider.health(model)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"AI provider check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Provider: {config.get('provider')} · model: {model}")
    typer.echo(message)
    if not healthy:
        if config.get("provider") == "ollama":
            typer.echo(f"Fix: install Ollama, start it with `ollama serve`, then run `ollama pull {model}`.")
        elif config.get("provider") == "gemini":
            typer.echo("Fix: set GEMINI_API_KEY from the Google AI Studio free tier and choose an available model.")
        elif config.get("provider") == "anthropic":
            typer.echo("Fix: set ANTHROPIC_API_KEY and select a model listed by the Anthropic API.")
        else:
            typer.echo("Fix: check [ai].base_url and the provider key environment variable.")
        raise typer.Exit(code=1)


@ai_app.command("consent")
def ai_consent(
    provider: str = typer.Option(
        "ollama",
        help="Provider receiving content: ollama, gemini, openai_compatible, or anthropic.",
    ),
) -> None:
    """Remember one-time consent for sending page content to a provider."""
    from testbench.ai.config import save_provider_consent

    accepted = typer.confirm(
        f"Page content and screenshots will be sent to {provider}. Use ollama for fully local processing. Continue?"
    )
    try:
        save_provider_consent(provider, accepted)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not save AI consent: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Consent {'saved' if accepted else 'not granted'} for {provider}.")


@ai_app.command("generate")
def ai_generate(
    instruction: str,
    target: str = typer.Option("web", help="web, android, or game."),
    url: str | None = typer.Option(None, help="Starting URL for web tests."),
    save: str | None = typer.Option(None, help="Save generated YAML under this test name."),
) -> None:
    """Generate a validated YAML test draft from plain English."""
    from testbench.ai.client import AIClient
    from testbench.ai.config import load_ai_config
    from testbench.ai.generate import generate_test
    from testbench.core.steps import Step
    from testbench.recorder.yaml_export import dump_test_yaml

    ai_run_id = uuid.uuid4().hex

    config = load_ai_config()
    try:
        draft = generate_test(
            instruction,
            target=target,
            url=url,
            client=AIClient(config),
            run_id=ai_run_id,
        )
        text = dump_test_yaml(
            name=draft["name"],
            target=draft["target"],
            config=draft["config"],
            variables=draft["variables"],
            steps=[Step.model_validate(item) for item in draft["steps"]],
        )
        text = "# TestBench-AI: generated\n" + text
        if save:
            filename = re.sub(r"[^A-Za-z0-9_-]+", "_", save).strip("_")
            if not filename:
                raise ValueError("--save must contain letters or numbers.")
            directory = Path(os.environ.get("TESTBENCH_TESTS_DIR", "tests_examples"))
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{filename}.yaml"
            path.write_text(text, encoding="utf-8")
            typer.echo(f"Saved generated test: {path.resolve()}")
        else:
            typer.echo(text, nl=False)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"AI generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@ai_app.command("explain")
def ai_explain(run_id: str) -> None:
    """Explain a failed run using rules first and optional AI second."""
    from testbench.ai.config import load_ai_config
    from testbench.ai.explain import analyze_failure, explain_run
    from testbench.core.database import RunStore

    database = Path(os.environ.get("TESTBENCH_DATABASE_PATH", "runs/testbench.sqlite3"))
    run = RunStore(database).get(run_id)
    if run is None:
        typer.echo(f"Run {run_id!r} was not found.", err=True)
        raise typer.Exit(code=1)
    failed = next((step for step in run["steps"] if step["status"] == "failed"), None)
    if failed is None:
        typer.echo("This run has no failed step.")
        return
    typer.echo(
        json.dumps(
            analyze_failure(
                failed.get("error") or "",
                target=run["target"],
                diagnostics=run.get("metadata", {}),
            ),
            indent=2,
        )
    )
    config = load_ai_config()
    if config.get("enabled"):
        from testbench.ai.client import AIClient

        try:
            result = explain_run(run, client=AIClient(config, database_path=database))
        except (OSError, RuntimeError, ValueError) as exc:
            typer.echo(f"AI explanation unavailable: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo("AI suggestion, may be wrong:")
        typer.echo(json.dumps(result, indent=2))


@ai_app.command("suggest")
def ai_suggest(test_file: Path) -> None:
    """Draft additional scenarios for an existing YAML test."""
    from testbench.ai.client import AIClient
    from testbench.ai.config import load_ai_config
    from testbench.ai.suggest import suggest_tests

    try:
        suggestions = suggest_tests(
            test_file,
            client=AIClient(load_ai_config()),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"AI suggestions unavailable: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(yaml.safe_dump(suggestions, sort_keys=False, allow_unicode=True), nl=False)


@heal_app.command("apply")
def heal_apply(run_id: str) -> None:
    """Apply previously accepted locator suggestions to their source YAML."""
    from testbench.core.database import RunStore
    from testbench.core.steps import TestSpec

    database = Path(os.environ.get("TESTBENCH_DATABASE_PATH", "runs/testbench.sqlite3"))
    store = RunStore(database)
    run = store.get(run_id)
    source = (run or {}).get("metadata", {}).get("source_test")
    if not source or not Path(source).is_file():
        typer.echo("The source YAML for this run is unavailable.", err=True)
        raise typer.Exit(code=1)
    accepted = [row for row in store.list_healing(run_id) if row["accepted"] is True]
    if not accepted:
        typer.echo("No accepted healing suggestions are available for this run.")
        return
    path = Path(source)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    TestSpec.model_validate(document)
    for row in accepted:
        action, value = next(iter(document["steps"][row["step_index"] - 1].items()))
        if action in {"click", "expect_visible"}:
            document["steps"][row["step_index"] - 1][action] = row["new_locator"]
        elif action in {"type", "select_option"}:
            value["field"] = row["new_locator"]
        elif action == "expect_enabled":
            value["target"] = row["new_locator"]
        else:
            typer.echo(f"Step {row['step_index']} has no supported locator target.", err=True)
            raise typer.Exit(code=1)
    try:
        TestSpec.model_validate(document)
        path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not apply healing fixes: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Applied {len(accepted)} accepted fix(es) to {path.resolve()}")


def _record_cli(target: str, config: dict) -> None:
    from testbench.recorder.yaml_export import dump_test_yaml

    try:
        if target == "web":
            from testbench.recorder.web_recorder import WebRecorder

            recorder_type = WebRecorder
        elif target == "android":
            from testbench.recorder.mobile_recorder import AndroidRecorder

            recorder_type = AndroidRecorder
        elif target == "game":
            from testbench.recorder.game_recorder import GameRecorder

            recorder_type = GameRecorder
        else:
            raise ValueError(f"Unsupported recorder target: {target}")
    except ImportError as exc:
        extra = "mobile" if target == "android" else "game"
        typer.echo(
            f"{target.title()} recording requires optional dependencies. "
            f'Install them with: pip install "testbench[{extra}]".',
            err=True,
        )
        raise typer.Exit(code=2) from exc
    tests_dir = Path(os.environ.get("TESTBENCH_TESTS_DIR", "tests_examples"))
    artifact_dir = Path("runs") / "recordings" / uuid.uuid4().hex
    config = {**config, "artifact_dir": str(artifact_dir)}
    stopped = False
    try:
        recorder = recorder_type()
        recorder.start(config)
        typer.echo("Recording. Interact with the target, then press Enter here to stop.")
        input()
        steps = recorder.stop()
        stopped = True
        suggested = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(config.get("url", target)).stem).strip("_")
        name = typer.prompt("Test name", default=suggested.title() or "Recorded test")
        filename = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "recorded_test"
        tests_dir.mkdir(parents=True, exist_ok=True)
        destination = tests_dir / f"{filename}.yaml"
        destination.write_text(
            dump_test_yaml(
                name=name,
                target=target,
                config={key: value for key, value in config.items() if key != "artifact_dir"},
                variables=recorder.variables,
                steps=steps,
            ),
            encoding="utf-8",
        )
        typer.echo(f"Saved recorded test: {destination.resolve()}")
    except (OSError, RuntimeError, ValueError, ImportError, KeyboardInterrupt) as exc:
        typer.echo(f"Recording failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        if "recorder" in locals() and not stopped:
            try:
                recorder.stop()
            except (OSError, RuntimeError, ValueError) as cleanup_error:
                typer.echo(f"Recorder cleanup failed: {cleanup_error}", err=True)


if __name__ == "__main__":
    app()
