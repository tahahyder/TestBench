"""Local TestBench API and dashboard."""

import base64
import hmac
import json
import os
import queue
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from testbench.core.database import RunStore
from testbench.core.runner import RunResult, StepResult, run_test
from testbench.core.steps import Step, TestSpec

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = PACKAGE_ROOT / "ui"
PROJECT_ROOT = Path.cwd().resolve()
TESTS_DIR = Path(os.environ.get("TESTBENCH_TESTS_DIR", str(PROJECT_ROOT / "tests_examples")))
RUNS_DIR = Path(os.environ.get("TESTBENCH_RUNS_DIR", str(PROJECT_ROOT / "runs")))
REPORTS_DIR = Path(os.environ.get("TESTBENCH_REPORTS_DIR", str(PROJECT_ROOT / "reports")))
DATABASE_PATH = Path(os.environ.get("TESTBENCH_DATABASE_PATH", str(RUNS_DIR / "testbench.sqlite3")))
GAME_TEMPLATES_DIR = Path(
    os.environ.get(
        "TESTBENCH_GAME_TEMPLATES_DIR",
        str(PROJECT_ROOT / "tests_examples" / "game_demo" / "templates"),
    )
)
STORE = RunStore(DATABASE_PATH)
_live_runs: dict[str, dict] = {}
_live_runs_lock = Lock()
_recorder_lock = Lock()
_recorder_state: dict | None = None
DeviceManager = None
MobileDriver = None
GameDriver = None
WebRecorder = None
AndroidRecorder = None
GameRecorder = None

app = FastAPI(title="TestBench", version="0.1.0")
app.mount("/reports", StaticFiles(directory=REPORTS_DIR, check_dir=False), name="reports")
app.mount("/runs", StaticFiles(directory=RUNS_DIR, check_dir=False), name="runs")
app.mount(
    "/static",
    StaticFiles(directory=UI_ROOT / "static"),
    name="static",
)


@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    """Require a token for configured remote access and reject cross-origin writes."""
    expected_token = os.environ.get("TESTBENCH_AUTH_TOKEN", "")
    if expected_token:
        supplied = ""
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        elif authorization.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                _, separator, supplied = decoded.partition(":")
                if not separator:
                    supplied = ""
            except (ValueError, UnicodeDecodeError):
                supplied = ""
        if not hmac.compare_digest(supplied, expected_token):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="TestBench"'},
                content="Authentication required.",
            )

    if request.url.path.startswith("/api/") and request.method not in {
        "GET",
        "HEAD",
        "OPTIONS",
    }:
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site", "")
        if origin:
            parsed = urlsplit(origin)
            expected_host = request.headers.get("host", "")
            if parsed.scheme != request.url.scheme or parsed.netloc.casefold() != expected_host.casefold():
                return Response(status_code=403, content="Cross-origin API request rejected.")
        elif fetch_site.casefold() == "cross-site":
            return Response(status_code=403, content="Cross-origin API request rejected.")
    return await call_next(request)


class RunRequest(BaseModel):
    """Request body for starting one YAML test."""

    path: str


class EmulatorStartRequest(BaseModel):
    """Request body to start one configured Android virtual device."""

    avd_name: str


class GameTemplateRequest(BaseModel):
    """Crop a rectangle from the current game surface into a template image."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=80)
    surface: Literal["desktop", "android", "browser"] = "desktop"
    window_title: str | None = None
    device: str = "auto"
    url: str | None = None
    templates_dir: str | None = None


class RecorderStartRequest(BaseModel):
    """Target settings for a live interaction recording."""

    target: Literal["web", "android", "game"]
    config: dict = Field(default_factory=dict)
    name: str = "Recorded test"


class RecorderStepsRequest(BaseModel):
    """Edited, normalized steps currently shown in the recorder UI."""

    steps: list[dict]


class RecorderSaveRequest(BaseModel):
    """Save current recorder steps as one YAML test."""

    name: str = Field(min_length=1, max_length=100)


class AIGenerateRequest(BaseModel):
    """Request a plain-English test draft."""

    instruction: str = Field(min_length=1, max_length=4000)
    target: Literal["web", "android", "game"] = "web"
    url: str | None = None
    config: dict = Field(default_factory=dict)
    iterate: bool = False


class AIYamlRequest(BaseModel):
    """YAML supplied to AI writer validation/dry-run/save routes."""

    yaml: str = Field(min_length=1, max_length=50000)
    ai_generated: bool = False
    ai_run_id: str | None = None


class AIConsentRequest(BaseModel):
    """Explicit consent for one provider to process page content."""

    provider: str
    accepted: bool


class AIExplainRequest(BaseModel):
    """Optional snapshot from the current failure report."""

    snapshot: str = Field(default="", max_length=20000)


class HealingDecisionRequest(BaseModel):
    """Accept or reject a stored healing suggestion."""

    accepted: bool


class AISuggestRequest(BaseModel):
    """Choose one local YAML test for additional scenario drafts."""

    test_name: str = Field(min_length=1, max_length=200)
    snapshot: str = Field(default="", max_length=20000)


class AIDataRequest(BaseModel):
    """Generate deterministic fake values for named fields."""

    fields: list[dict[str, str]]
    seed: int = 0
    context: str | None = Field(default=None, max_length=1000)


class RecorderReplayRequest(BaseModel):
    """Request immediate replay of the current recorder steps."""

    pass


class GameTemplateRenameRequest(BaseModel):
    """Rename one recorded image template without overwriting existing files."""

    name: str = Field(min_length=1, max_length=80)


@app.get("/mobile-inspector", response_class=FileResponse)
def mobile_inspector_page() -> FileResponse:
    """Serve the Android locator inspector."""
    return FileResponse(UI_ROOT / "templates" / "mobile-inspector.html")


@app.get("/game-template-maker", response_class=FileResponse)
def game_template_maker_page() -> FileResponse:
    """Serve the game template capture and matching page."""
    return FileResponse(UI_ROOT / "templates" / "game-template-maker.html")


@app.get("/", response_class=FileResponse)
def dashboard() -> FileResponse:
    """Serve the local dashboard."""
    return FileResponse(UI_ROOT / "templates" / "index.html")


@app.get("/history", response_class=FileResponse)
def history_page() -> FileResponse:
    """Serve the local run-history trend page."""
    return FileResponse(UI_ROOT / "templates" / "history.html")


@app.get("/ai-writer", response_class=FileResponse)
def ai_writer_page() -> FileResponse:
    """Serve the opt-in AI test authoring page."""
    return FileResponse(UI_ROOT / "templates" / "ai-writer.html")


@app.get("/api/ai/config")
def ai_configuration() -> dict:
    """Expose non-secret AI settings and consent state to the local dashboard."""
    from testbench.ai.config import load_ai_config

    config = load_ai_config()
    return {
        "enabled": bool(config.get("enabled")),
        "provider": config.get("provider"),
        "model": config.get("model"),
        "consented": config.get("provider") in config.get("consented_providers", []),
    }


@app.get("/api/health")
def local_health() -> dict[str, Any]:
    """Return a local, non-network health summary for the welcome dashboard."""
    from testbench.setup.doctor import _playwright_browser_available

    python_ok = sys.version_info >= (3, 11)
    browser_ok = _playwright_browser_available()
    return {
        "web_ready": python_ok and browser_ok,
        "python_version": sys.version.split()[0],
        "python_ok": python_ok,
        "playwright_browser": browser_ok,
        "web_message": (
            "Run `testbench doctor` for exact setup instructions."
            if not (python_ok and browser_ok)
            else "Playwright Chromium is installed."
        ),
    }


@app.post("/api/ai/consent")
def save_ai_consent(request: AIConsentRequest) -> dict:
    """Persist explicit, per-provider consent without storing credentials."""
    from testbench.ai.config import save_provider_consent

    path = Path(os.environ.get("TESTBENCH_CONFIG", PROJECT_ROOT / "testbench.toml"))
    try:
        save_provider_consent(request.provider, request.accepted, path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"provider": request.provider, "consented": request.accepted}


@app.post("/api/ai/generate")
def generate_ai_test(request: AIGenerateRequest) -> dict:
    """Generate and return a validated YAML test draft."""
    from testbench.ai.client import AIClient
    from testbench.ai.config import load_ai_config
    from testbench.ai.generate import generate_test, iterate_test
    from testbench.recorder.yaml_export import dump_test_yaml

    config = load_ai_config()
    ai_run_id = uuid.uuid4().hex
    try:
        client = AIClient(config, database_path=DATABASE_PATH)
        attempts = []
        if request.iterate:
            iteration = iterate_test(
                request.instruction,
                target=request.target,
                url=request.url,
                config=request.config,
                client=client,
                run_id=ai_run_id,
            )
            test = iteration["test"]
            attempts = iteration["attempts"]
        else:
            test = generate_test(
                request.instruction,
                target=request.target,
                url=request.url,
                config=request.config,
                client=client,
                run_id=ai_run_id,
            )
        text = dump_test_yaml(
            name=test["name"],
            target=test["target"],
            config=test["config"],
            variables=test["variables"],
            steps=[Step.model_validate(item) for item in test["steps"]],
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "yaml": text,
        "test": test,
        "attempts": attempts,
        "ai_run_id": ai_run_id,
        "provider": config.get("provider"),
        "model": config.get("model"),
    }


@app.post("/api/ai/validate")
def validate_ai_yaml(request: AIYamlRequest) -> dict:
    """Validate editor YAML with the normal TestSpec and Step models."""
    try:
        raw = yaml.safe_load(request.yaml)
        spec = TestSpec.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid TestBench YAML: {exc}") from exc
    return {
        "valid": True,
        "name": spec.name,
        "target": spec.target,
        "steps": len(spec.steps),
        "yaml": request.yaml,
    }


@app.post("/api/ai/dry-run")
def dry_run_ai_yaml(request: AIYamlRequest) -> dict:
    """Run a validated editor draft using the normal runner and drivers."""
    try:
        raw = yaml.safe_load(request.yaml)
        TestSpec.model_validate(raw)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ai-draft-", dir=RUNS_DIR) as directory:
            path = Path(directory) / "draft.yaml"
            path.write_text(request.yaml, encoding="utf-8")
            result = run_test(
                path,
                runs_dir=RUNS_DIR,
                reports_dir=REPORTS_DIR,
                database_path=DATABASE_PATH,
                ai_generated=request.ai_generated,
                ai_run_id=request.ai_run_id,
            )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"AI draft dry-run failed: {exc}") from exc
    return {"status": result.status, "run_id": result.run_id, "report_path": result.report_path}


@app.post("/api/ai/save")
def save_ai_yaml(request: AIYamlRequest, name: str = Query(min_length=1, max_length=100)) -> dict:
    """Save a validated AI draft into the local tests folder."""
    try:
        spec = TestSpec.model_validate(yaml.safe_load(request.yaml))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid TestBench YAML: {exc}") from exc
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    if not filename:
        raise HTTPException(status_code=422, detail="Test name must contain letters or numbers.")
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = (TESTS_DIR / f"{filename}.yaml").resolve()
    if path.parent != TESTS_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid test name.")
    text = request.yaml
    if request.ai_generated and not text.startswith("# TestBench-AI: generated"):
        text = "# TestBench-AI: generated\n" + text
    path.write_text(text, encoding="utf-8")
    return {"name": spec.name, "path": str(path)}


@app.post("/api/ai/suggest")
def suggest_ai_tests(request: AISuggestRequest) -> dict:
    """Return extra scenario drafts for one local YAML test."""
    from testbench.ai.client import AIClient
    from testbench.ai.config import load_ai_config
    from testbench.ai.suggest import suggest_tests
    from testbench.recorder.yaml_export import dump_test_yaml

    test_path = (TESTS_DIR / request.test_name).resolve()
    if test_path.parent != TESTS_DIR.resolve() or not test_path.is_file():
        raise HTTPException(status_code=404, detail="Test file not found.")
    try:
        drafts = suggest_tests(
            test_path,
            client=AIClient(load_ai_config(), database_path=DATABASE_PATH),
            snapshot=request.snapshot or None,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "drafts": [
            {
                "test": draft,
                "yaml": dump_test_yaml(
                    name=draft["name"],
                    target=draft["target"],
                    config=draft["config"],
                    variables=draft["variables"],
                    steps=[Step.model_validate(item) for item in draft["steps"]],
                ),
            }
            for draft in drafts
        ]
    }


@app.post("/api/ai/data")
def generate_ai_data(request: AIDataRequest) -> dict:
    """Generate deterministic fake data locally through Faker."""
    from testbench.ai.suggest import generate_fake_data

    try:
        if request.context:
            from testbench.ai.client import AIClient
            from testbench.ai.config import load_ai_config

            data = generate_fake_data(
                request.fields,
                seed=request.seed,
                context=request.context,
                client=AIClient(load_ai_config(), database_path=DATABASE_PATH),
            )
        else:
            data = generate_fake_data(request.fields, seed=request.seed)
        return {"data": data}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/ai/healing")
def ai_healing_history(run_id: str | None = None) -> list[dict]:
    """List stored locator healing history."""
    return STORE.list_healing(run_id)


@app.post("/api/ai/healing/{record_id}/decision")
def decide_healing(record_id: int, request: HealingDecisionRequest) -> dict:
    """Record whether a locator suggestion was accepted."""
    if not STORE.update_healing(record_id, accepted=request.accepted):
        raise HTTPException(status_code=404, detail="Healing suggestion not found.")
    return {"id": record_id, "accepted": request.accepted}


@app.post("/api/ai/healing/{record_id}/apply")
def apply_healing(record_id: int) -> dict:
    """Patch one accepted healing suggestion into its source YAML file."""
    history = next(
        (item for item in STORE.list_healing(limit=10000) if item["id"] == record_id),
        None,
    )
    if history is None:
        raise HTTPException(status_code=404, detail="Healing suggestion not found.")
    if history["accepted"] is not True:
        raise HTTPException(status_code=409, detail="Accept this suggestion before applying it.")
    run = STORE.get(history["run_id"])
    source = (run or {}).get("metadata", {}).get("source_test")
    if not source or not Path(source).is_file():
        raise HTTPException(status_code=404, detail="Source YAML for this run is unavailable.")
    try:
        document = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
        step = document["steps"][history["step_index"] - 1]
        action, value = next(iter(step.items()))
        if action in {"click", "expect_visible"}:
            step[action] = history["new_locator"]
        elif action in {"type", "select_option"}:
            value["field"] = history["new_locator"]
        elif action == "expect_enabled":
            value["target"] = history["new_locator"]
        else:
            raise ValueError(f"Step {action} does not contain an applicable locator.")
        Path(source).write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not apply healing fix: {exc}") from exc
    return {"path": source, "step_index": history["step_index"]}


@app.post("/api/runs/{run_id}/explain")
def explain_failed_run(run_id: str, request: AIExplainRequest | None = None) -> dict:
    """Return a rule-based explanation and, with consent, an AI explanation."""
    from testbench.ai.config import load_ai_config
    from testbench.ai.explain import analyze_failure, explain_run

    run = STORE.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    failed = next((step for step in run["steps"] if step["status"] == "failed"), None)
    if failed is None:
        raise HTTPException(status_code=409, detail="Run has no failed step.")
    rule = analyze_failure(
        failed.get("error") or "",
        target=run["target"],
        diagnostics=run.get("metadata", {}),
    )
    config = load_ai_config()
    response = {"rule_based": rule, "ai": None}
    if config.get("enabled"):
        from testbench.ai.client import AIClient

        try:
            ai_client = AIClient(config, database_path=DATABASE_PATH)
            ai_result = explain_run(
                run,
                client=ai_client,
                snapshot_extra=(request.snapshot if request else ""),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        STORE.update_metadata(
            run_id,
            {
                "ai_failure_explanation": ai_result,
                "ai_usage": ai_client.usage(run_id),
                "ai_provider": config.get("provider"),
                "ai_model": config.get("model"),
            },
        )
        refreshed = STORE.get(run_id)
        if refreshed and refreshed.get("report_path"):
            _regenerate_stored_report(refreshed)
        response["ai"] = ai_result
    return response


@app.get("/recorder", response_class=FileResponse)
def recorder_page() -> FileResponse:
    """Serve the live interaction recorder."""
    return FileResponse(UI_ROOT / "templates" / "recorder.html")


@app.post("/api/recorder/start", status_code=202)
def start_recorder(request: RecorderStartRequest) -> dict:
    """Start one recorder in its own thread, preserving Playwright thread affinity."""
    global _recorder_state
    with _recorder_lock:
        if _recorder_state and _recorder_state.get("thread", None):
            thread = _recorder_state["thread"]
            if thread.is_alive():
                raise HTTPException(status_code=409, detail="A recording is already active.")

        events: queue.Queue = queue.Queue()
        ready = threading.Event()
        stop_event = threading.Event()
        state = {
            "target": request.target,
            "name": request.name,
            "config": dict(request.config),
            "events": events,
            "ready": ready,
            "stop_event": stop_event,
            "status": "starting",
            "steps": [],
            "warnings": [],
            "error": None,
            "recorder": None,
            "thread": None,
            "self_check": None,
        }
        _recorder_state = state
        worker = threading.Thread(
            target=_recorder_worker,
            args=(state,),
            name="testbench-recorder",
            daemon=True,
        )
        state["thread"] = worker
        worker.start()

    if not ready.wait(timeout=30):
        stop_event.set()
        raise HTTPException(status_code=504, detail="Recorder did not initialize in time.")
    if state["error"]:
        raise HTTPException(status_code=503, detail=state["error"])
    return {"status": state["status"], "target": request.target}


@app.post("/api/recorder/stop")
def stop_recorder() -> dict:
    """Stop the active recorder, normalize steps, and schedule a replay check."""
    state = _require_recorder()
    state["stop_event"].set()
    thread = state["thread"]
    thread.join(timeout=30)
    if thread.is_alive():
        raise HTTPException(status_code=504, detail="Recorder did not stop in time.")
    if state["error"]:
        raise HTTPException(status_code=500, detail=state["error"])
    state["self_check"] = {"status": "queued"}
    threading.Thread(
        target=_recorder_self_check,
        args=(state,),
        name="testbench-recorder-self-check",
        daemon=True,
    ).start()
    return {
        "status": "stopped",
        "steps": state["steps"],
        "warnings": state["warnings"],
        "self_check": state["self_check"],
    }


@app.post("/api/recorder/pause")
def pause_recorder() -> dict[str, str]:
    state = _require_recorder()
    recorder = state.get("recorder")
    if recorder is None:
        raise HTTPException(status_code=409, detail="Recorder is not ready.")
    recorder.pause()
    state["status"] = "paused"
    return {"status": "paused"}


@app.post("/api/recorder/resume")
def resume_recorder() -> dict[str, str]:
    state = _require_recorder()
    recorder = state.get("recorder")
    if recorder is None:
        raise HTTPException(status_code=409, detail="Recorder is not ready.")
    recorder.resume()
    state["status"] = "recording"
    return {"status": "recording"}


@app.get("/api/recorder")
def recorder_status() -> dict:
    """Return recording status, current steps, and replay self-check result."""
    state = _require_recorder(allow_stopped=True)
    return {
        "status": state["status"],
        "steps": state["steps"],
        "warnings": state["warnings"],
        "error": state["error"],
        "self_check": state["self_check"],
    }


@app.get("/api/recorder/events")
def recorder_events() -> StreamingResponse:
    """Stream recorder events using Server-Sent Events."""
    state = _require_recorder()

    def stream():
        events = state["events"]
        while True:
            try:
                event = events.get(timeout=0.5)
                yield f"data: {json.dumps(event, default=str)}\n\n"
            except queue.Empty:
                if state["status"] in {"stopped", "failed"}:
                    yield "event: close\ndata: {}\n\n"
                    break

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.patch("/api/recorder/steps")
def edit_recorder_steps(request: RecorderStepsRequest) -> dict:
    """Validate and replace the editable recorder step list."""
    state = _require_recorder(allow_stopped=True)
    normalized = []
    try:
        for raw in request.steps:
            step = Step.model_validate(raw)
            normalized.append({step.action: step.value})
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid recorded step: {exc}") from exc
    state["steps"] = normalized
    state["events"].put({"kind": "steps_edited", "steps": normalized})
    return {"steps": normalized}


@app.post("/api/recorder/save")
def save_recorder_test(request: RecorderSaveRequest) -> dict[str, str]:
    """Persist the current recording as a YAML file in the tests directory."""
    state = _require_recorder(allow_stopped=True)
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", request.name).strip("_")
    if not name:
        raise HTTPException(status_code=422, detail="Test name must contain letters or numbers.")
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = (TESTS_DIR / f"{name}.yaml").resolve()
    if path.parent != TESTS_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid test name.")
    from testbench.recorder.yaml_export import dump_test_yaml

    try:
        text = dump_test_yaml(
            name=request.name,
            target=state["target"],
            config=state["config"],
            variables=state["recorder"].variables if state.get("recorder") else [],
            steps=[Step.model_validate(item) for item in state["steps"]],
        )
        path.write_text(text, encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not save test: {exc}") from exc
    return {"name": request.name, "path": str(path)}


@app.post("/api/recorder/replay")
def replay_recorder() -> dict:
    """Replay the current steps and return its report/result."""
    state = _require_recorder(allow_stopped=True)
    return _run_recorder_replay(state)


@app.get("/api/tests")
def list_tests() -> list[dict[str, str]]:
    """List YAML files available in the configured tests folder."""
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    return [{"name": test_file.stem, "path": test_file.name} for test_file in sorted(TESTS_DIR.glob("*.yaml"))]


@app.post("/api/tests/{test_name}/run", status_code=202)
def start_test(test_name: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Start a named YAML test as a background task."""
    if not test_name or Path(test_name).name != test_name:
        raise HTTPException(status_code=400, detail="Invalid test name.")
    test_path = (TESTS_DIR / f"{test_name}.yaml").resolve()
    if test_path.parent != TESTS_DIR.resolve() or not test_path.is_file():
        raise HTTPException(status_code=404, detail="Test not found.")
    run_id = uuid.uuid4().hex
    with _live_runs_lock:
        _live_runs[run_id] = {
            "run_id": run_id,
            "test_name": test_name,
            "status": "queued",
            "steps": [],
        }
    background_tasks.add_task(_run_in_background, run_id, test_path)
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/runs")
def list_runs(limit: int = 50) -> list[dict]:
    """List recent persisted runs."""
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200.")
    return _redact_response(STORE.list(limit))


@app.get("/api/history/trends")
def history_trends() -> dict[str, Any]:
    """Summarize recent pass rate, duration, and flaky-run history locally."""
    from collections import defaultdict

    runs = STORE.list(200)
    daily: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_test: dict[str, dict[str, Any]] = {}
    for run in runs:
        day = str(run.get("started_at", ""))[:10]
        if day:
            daily[day][1] += 1
            daily[day][0] += int(run.get("status") == "passed")
        name = str(run.get("test_name", "unknown"))
        item = by_test.setdefault(name, {"test_name": name, "runs": 0, "flaky_runs": 0, "duration": 0.0})
        item["runs"] += 1
        item["duration"] = max(item["duration"], float(run.get("duration_seconds", 0)))
        item["flaky_runs"] += int(bool(run.get("metadata", {}).get("flaky")))
    slowest = sorted(by_test.values(), key=lambda item: item["duration"], reverse=True)[:5]
    flakiest = sorted(
        (item for item in by_test.values() if item["flaky_runs"]),
        key=lambda item: item["flaky_runs"],
        reverse=True,
    )[:5]
    points = [
        {
            "date": day,
            "passed": counts[0],
            "total": counts[1],
            "pass_rate": round(counts[0] / counts[1], 3),
        }
        for day, counts in sorted(daily.items())
    ]
    return _redact_response({"runs": len(runs), "pass_rate": points, "slowest": slowest, "flakiest": flakiest})


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Get live progress or the persisted result of a run."""
    with _live_runs_lock:
        live_result = _live_runs.get(run_id)
    if live_result is not None:
        return _redact_response(live_result)
    result = STORE.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return _redact_response(result)


@app.get("/api/runs/{run_id}/report")
def get_report(run_id: str) -> FileResponse:
    """Serve a generated static HTML report."""
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise HTTPException(status_code=400, detail="Invalid run ID.")
    report_path = REPORTS_DIR / run_id / "index.html"
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(report_path, media_type="text/html")


@app.get("/api/devices")
def list_android_devices() -> dict:
    """List connected devices and local Android virtual devices."""
    try:
        manager_type = _optional_symbol("DeviceManager", "testbench.drivers.devices")
        manager = manager_type()
        return {"devices": manager.list_devices(), "avds": manager.list_avds()}
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/devices/emulator/start")
def start_android_emulator(request: EmulatorStartRequest) -> dict[str, str]:
    """Start an AVD and wait until Android finishes booting."""
    try:
        serial = _optional_symbol("DeviceManager", "testbench.drivers.devices")().start_emulator(request.avd_name)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"avd_name": request.avd_name, "serial": serial, "status": "booted"}


@app.get("/api/mobile/inspect")
def inspect_mobile_device(
    device: str = Query(default="auto"),
    appium_url: str = Query(default="http://127.0.0.1:4723"),
) -> dict:
    """Capture Android screen and hierarchy from a short-lived Appium session."""
    try:
        mobile_driver = _optional_symbol("MobileDriver", "testbench.drivers.mobile")
        from selenium.common.exceptions import WebDriverException
    except (ImportError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail='Android support is optional. Install it with: pip install "testbench[mobile]".',
        ) from exc

    with tempfile.TemporaryDirectory(prefix="testbench-inspector-") as directory:
        driver = mobile_driver(
            {
                "appium_url": appium_url,
                "device": device,
                "package": "com.android.settings",
                "reset": False,
            },
            Path(directory),
        )
        response = None
        error = None
        try:
            driver.open("")
            captured = driver.inspect(Path(directory) / "screen.png")
            screenshot = Path(captured["screenshot_path"]).read_bytes()
            response = {
                "screenshot": "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii"),
                "elements": captured["elements"],
                "metadata": captured["metadata"],
            }
        except (OSError, RuntimeError, ValueError, WebDriverException) as exc:
            error = exc
        try:
            driver.close()
        except (OSError, RuntimeError, WebDriverException) as close_error:
            if error is None:
                error = close_error
        if error is not None:
            raise HTTPException(
                status_code=503,
                detail=f"Could not inspect Android device: {error}",
            ) from error
        assert response is not None
        return response


@app.get("/api/game/screenshot")
def capture_game_screenshot(
    surface: Literal["desktop", "android", "browser"] = "desktop",
    window_title: str | None = None,
    device: str = "auto",
    url: str | None = None,
    templates_dir: str | None = None,
) -> Response:
    """Capture a PNG from the selected game surface."""
    cv2 = _game_image_modules()
    config = _game_options(surface, window_title, device, url, templates_dir)
    try:
        image = _capture_game_frame(config)
    except (OSError, RuntimeError, ValueError, LookupError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not capture game screenshot: {exc}",
        ) from exc
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise HTTPException(status_code=500, detail="Could not encode game screenshot.")
    return Response(content=encoded.tobytes(), media_type="image/png")


@app.get("/api/game/templates")
def list_game_templates(
    templates_dir: str | None = None,
) -> list[dict[str, str]]:
    """List saved game templates in the selected directory."""
    directory = _safe_templates_dir(templates_dir)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="templates_dir must be a directory.")
    return [
        {
            "name": item.name,
            "image_url": f"/api/game/templates/{item.name}/image"
            + ("?" + urlencode({"templates_dir": str(directory)}) if templates_dir else ""),
        }
        for item in sorted(directory.iterdir())
        if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]


@app.get("/api/game/templates/{name}/image")
def get_game_template(
    name: str,
    templates_dir: str | None = None,
) -> FileResponse:
    """Serve one saved template image."""
    _validate_template_name(name)
    directory = _safe_templates_dir(templates_dir)
    image_path = directory / name
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Game template not found.")
    media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(image_path, media_type=media_type)


@app.post("/api/game/templates/{name}/rename")
def rename_game_template(
    name: str,
    request: GameTemplateRenameRequest,
    templates_dir: str | None = None,
) -> dict[str, str]:
    """Rename an existing local game template and return its YAML step."""
    _validate_template_name(name)
    directory = _safe_templates_dir(templates_dir)
    source = (directory / name).resolve()
    if source.parent != directory.resolve() or not source.is_file():
        raise HTTPException(status_code=404, detail="Game template not found.")
    destination_name = _template_filename(request.name)
    destination = (directory / destination_name).resolve()
    if destination.exists():
        raise HTTPException(status_code=409, detail="A template with that name already exists.")
    source.rename(destination)
    return {
        "name": destination_name,
        "path": str(destination),
        "yaml_step": f"- click_image: {destination_name}",
    }


@app.post("/api/game/templates")
def save_game_template(request: GameTemplateRequest) -> dict[str, str]:
    """Crop and save a PNG template from a fresh game screenshot."""
    name = _template_filename(request.name)
    config = _game_options(
        request.surface,
        request.window_title,
        request.device,
        request.url,
        request.templates_dir,
    )
    try:
        screenshot = _capture_game_frame(config)
    except (OSError, RuntimeError, ValueError, LookupError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not capture game screenshot: {exc}",
        ) from exc

    height, width = screenshot.shape[:2]
    if request.x + request.width > width or request.y + request.height > height:
        raise HTTPException(
            status_code=422,
            detail=f"Crop rectangle must fit within the {width}x{height} screenshot.",
        )
    directory = _safe_templates_dir(config["templates_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    crop = screenshot[
        request.y : request.y + request.height,
        request.x : request.x + request.width,
    ]
    cv2 = _game_image_modules()
    if not cv2.imwrite(str(destination), crop):
        raise HTTPException(status_code=500, detail="Could not write game template.")
    return {
        "name": name,
        "path": str(destination.resolve()),
        "yaml_step": f"- click_image: {name}",
    }


@app.post("/api/game/templates/{name}/match")
def match_game_template(
    name: str,
    surface: Literal["desktop", "android", "browser"] = "desktop",
    window_title: str | None = None,
    device: str = "auto",
    url: str | None = None,
    templates_dir: str | None = None,
) -> dict:
    """Match one saved template against the current game screenshot."""
    try:
        game_driver = _optional_symbol("GameDriver", "testbench.drivers.game")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _validate_template_name(name)
    config = _game_options(surface, window_title, device, url, templates_dir)
    with tempfile.TemporaryDirectory(prefix="testbench-game-match-") as directory:
        driver = game_driver(config, Path(directory), recording=False)
        try:
            driver.open(url or "")
            return driver.match_now(name)
        except (OSError, RuntimeError, ValueError, LookupError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Could not test game template: {exc}",
            ) from exc
        finally:
            driver.close()


def _game_options(
    surface: Literal["desktop", "android", "browser"],
    window_title: str | None,
    device: str,
    url: str | None,
    templates_dir: str | None,
) -> dict:
    safe_templates = _safe_templates_dir(templates_dir)
    return {
        "surface": surface,
        "window_title": window_title,
        "device": device,
        "url": url,
        "templates_dir": str(safe_templates),
    }


def _safe_templates_dir(value: str | None) -> Path:
    candidate = Path(value).expanduser().resolve() if value else Path(GAME_TEMPLATES_DIR).expanduser().resolve()
    configured = Path(GAME_TEMPLATES_DIR).expanduser().resolve()
    project = PROJECT_ROOT.resolve()
    if candidate != configured and configured not in candidate.parents and project not in candidate.parents:
        raise HTTPException(
            status_code=400,
            detail="templates_dir must be inside the project directory or configured templates directory.",
        )
    return candidate


def _redact_response(value):
    from testbench.ai.redaction import redact_data

    return redact_data(value)[0]


def _optional_symbol(name: str, module_name: str):
    """Load target-specific code on demand while keeping dashboard imports light."""
    registered = globals().get(name)
    if registered is not None:
        return registered
    try:
        module = __import__(module_name, fromlist=[name])
    except ModuleNotFoundError as exc:
        extra = "mobile" if "mobile" in module_name or "devices" in module_name else "game"
        raise RuntimeError(
            f'This feature needs optional dependencies. Install them with: pip install "testbench[{extra}]".'
        ) from exc
    return getattr(module, name)


def _game_image_modules():
    try:
        import cv2
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail='Game support is optional. Install it with: pip install "testbench[game]".',
        ) from exc
    return cv2


def _capture_game_frame(config: dict):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError('Game support is optional. Install it with: pip install "testbench[game]".') from exc
    game_driver = _optional_symbol("GameDriver", "testbench.drivers.game")

    with tempfile.TemporaryDirectory(prefix="testbench-game-capture-") as directory:
        driver = game_driver(config, Path(directory), recording=False)
        try:
            driver.open(config.get("url") or "")
            screenshot_path = Path(directory) / "screen.png"
            driver.screenshot(screenshot_path)
            frame = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
        finally:
            driver.close()
        if frame is None:
            raise OSError("Captured game screenshot could not be decoded.")
        return frame


def _template_filename(value: str) -> str:
    if Path(value).name != value or "/" in value or "\\" in value:
        raise HTTPException(status_code=422, detail="Template name must be a filename.")
    normalized = Path(value).name
    stem = Path(normalized).stem
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", stem):
        raise HTTPException(
            status_code=422,
            detail="Template name must contain only letters, numbers, underscores, and hyphens.",
        )
    suffix = Path(normalized).suffix.lower()
    if suffix not in {"", ".png"}:
        raise HTTPException(status_code=422, detail="Game templates must be PNG images.")
    return f"{stem}.png"


def _validate_template_name(name: str) -> None:
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.(?:png|jpe?g)",
        name,
        flags=re.IGNORECASE,
    ):
        raise HTTPException(status_code=400, detail="Invalid game template name.")


def _write_ai_consent(path: Path, providers: list[str]) -> None:
    """Update only consented_providers in [ai], preserving unrelated TOML text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    value = "consented_providers = [" + ", ".join(json.dumps(provider) for provider in providers) + "]"
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
    path.write_text(content, encoding="utf-8")


def _regenerate_stored_report(run: dict) -> None:
    """Refresh a report after adding an optional AI explanation."""
    result = RunResult(
        run_id=run["run_id"],
        test_name=run["test_name"],
        target=run["target"],
        status=run["status"],
        started_at=run["started_at"],
        duration_seconds=run["duration_seconds"],
        steps=[
            StepResult(
                index=item["index"],
                action=item["action"],
                status=item["status"],
                duration_seconds=item["duration_seconds"],
                error=item.get("error"),
                screenshot=item.get("screenshot"),
            )
            for item in run["steps"]
        ],
        artifact_dir=run["artifact_dir"],
        report_path=run.get("report_path"),
        metadata=run.get("metadata", {}),
    )
    from testbench.core.reporter import generate_report

    generate_report(result, REPORTS_DIR)


def _require_recorder(*, allow_stopped: bool = False) -> dict:
    state = _recorder_state
    if state is None or state["status"] == "failed" or (not allow_stopped and state["status"] == "stopped"):
        raise HTTPException(status_code=409, detail="No active recorder session.")
    return state


def _recorder_worker(state: dict) -> None:
    recorder = None

    def on_event(event: dict) -> None:
        raw_event = event.get("event")
        if isinstance(raw_event, dict) and raw_event.get("screenshot"):
            try:
                relative = Path(raw_event["screenshot"]).resolve().relative_to(RUNS_DIR.resolve())
                raw_event["screenshot_url"] = "/runs/" + relative.as_posix()
            except ValueError:
                raw_event["screenshot_url"] = None
        state["events"].put(event)
        if recorder is not None:
            from testbench.recorder.step_builder import StepBuilder

            steps = StepBuilder().build(recorder.raw_events)
            state["steps"] = [{step.action: step.value} for step in steps]
            state["events"].put({"kind": "steps", "steps": state["steps"]})

    try:
        if state["target"] == "web":
            recorder_type = _optional_symbol("WebRecorder", "testbench.recorder.web_recorder")
        elif state["target"] == "android":
            try:
                recorder_type = _optional_symbol("AndroidRecorder", "testbench.recorder.mobile_recorder")
            except ImportError as exc:
                raise RuntimeError(
                    'Android recording requires the mobile extra: pip install "testbench[mobile]".'
                ) from exc
        else:
            try:
                recorder_type = _optional_symbol("GameRecorder", "testbench.recorder.game_recorder")
            except ImportError as exc:
                raise RuntimeError('Game recording requires the game extra: pip install "testbench[game]".') from exc
        artifact_dir = RUNS_DIR / "recordings" / uuid.uuid4().hex
        state["artifact_dir"] = artifact_dir
        config = {**state["config"], "artifact_dir": str(artifact_dir)}
        if state["target"] == "game":
            config.setdefault("templates_dir", str(GAME_TEMPLATES_DIR))
        recorder = recorder_type(on_event=on_event)
        state["recorder"] = recorder
        recorder.start(config)
        state["status"] = "recording"
        state["events"].put({"kind": "status", "status": "recording"})
    except Exception as exc:
        if recorder is not None:
            try:
                recorder.stop()
            except Exception as cleanup_error:
                state["warnings"].append(f"Recorder cleanup after startup failure also failed: {cleanup_error}")
        state["error"] = f"{type(exc).__name__}: {exc}"
        state["status"] = "failed"
        state["events"].put({"kind": "error", "detail": state["error"]})
        state["ready"].set()
        return
    state["ready"].set()
    try:
        while not state["stop_event"].wait(0.1):
            if getattr(recorder, "stop_requested", False):
                break
        final_steps = recorder.stop()
        state["steps"] = [{step.action: step.value} for step in final_steps]
        state["warnings"] = list(recorder.warnings)
        state["status"] = "stopped"
        state["events"].put(
            {
                "kind": "stopped",
                "steps": state["steps"],
                "warnings": state["warnings"],
            }
        )
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
        state["status"] = "failed"
        state["events"].put({"kind": "error", "detail": state["error"]})


def _recorder_self_check(state: dict) -> None:
    try:
        result = _run_recorder_replay(state)
        state["self_check"] = {
            "status": result["status"],
            "run_id": result["run_id"],
            "report_path": result.get("report_path"),
            "flakiness_warnings": _flakiness_warnings(state["steps"]),
        }
    except Exception as exc:
        state["self_check"] = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "flakiness_warnings": _flakiness_warnings(state["steps"]),
        }


def _run_recorder_replay(state: dict) -> dict:
    from testbench.recorder.yaml_export import dump_test_yaml

    with tempfile.TemporaryDirectory(prefix="testbench-recorder-replay-") as directory:
        test_path = Path(directory) / "recorded.yaml"
        text = dump_test_yaml(
            name=state["name"],
            target=state["target"],
            config=state["config"],
            variables=state["recorder"].variables if state.get("recorder") else [],
            steps=[Step.model_validate(item) for item in state["steps"]],
        )
        test_path.write_text(text, encoding="utf-8")
        result = run_test(
            test_path,
            runs_dir=RUNS_DIR,
            reports_dir=REPORTS_DIR,
            database_path=DATABASE_PATH,
        )
    return result.to_dict()


def _flakiness_warnings(steps: list[dict]) -> list[str]:
    warnings = []

    def contains_locator(value: Any, keys: set[str]) -> bool:
        if isinstance(value, dict):
            return bool(keys.intersection(value)) or any(contains_locator(child, keys) for child in value.values())
        if isinstance(value, list):
            return any(contains_locator(child, keys) for child in value)
        return False

    for index, raw in enumerate(steps, start=1):
        step = Step.model_validate(raw)
        value = step.value
        if contains_locator(value, {"css", "xpath"}):
            warnings.append(f"Step {index} uses a CSS/XPath locator; prefer a role, label, or test id.")
        if step.action == "wait":
            warnings.append(f"Step {index} uses a fixed wait; replace it with a visible-state assertion.")
        if step.action == "tap":
            warnings.append(f"Step {index} uses coordinates; prefer a stable element or image locator.")
    return warnings


def _run_in_background(run_id: str, test_path: Path) -> None:
    def update(result: RunResult) -> None:
        serialized = result.to_dict()
        with _live_runs_lock:
            _live_runs[run_id] = serialized
        STORE.save(result)

    try:
        run_test(
            test_path,
            runs_dir=RUNS_DIR,
            reports_dir=REPORTS_DIR,
            database_path=DATABASE_PATH,
            run_id=run_id,
            on_progress=update,
        )
    except Exception as exc:
        with _live_runs_lock:
            current = _live_runs[run_id]
            current.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
