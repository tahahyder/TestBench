# TestBench

TestBench is a free, open-source, local-first workbench for writing and running
web, Android, and game tests in one readable YAML format. It is a friendly layer
on top of Playwright, Appium, and OpenCV—not a replacement for those engines.
Tests, run history, screenshots, recordings, and HTML reports stay on your
machine; telemetry is not collected.

## 30-second quick start

```sh
pipx install testbench
pipx run --spec testbench python -m playwright install chromium
testbench init demo
cd demo
testbench doctor
testbench run tests/sample_web.yaml
testbench serve
```

Python 3.11+ is required. `testbench init` creates a public demo-site test and
local project folders. Docker is supported for web testing only; mobile and
desktop game testing need access to local devices or displays.

## Features

| Capability | What TestBench provides |
| --- | --- |
| One YAML format | Common readable steps across web, Android, and games |
| Existing engines | Playwright, Appium, and OpenCV behind drivers |
| Recorder | Capture interactions, review steps, and save YAML |
| Local dashboard | Run tests, review history, and open static reports |
| Optional AI | Off by default; provider consent and redaction required |
| CLI and CI | Folder/glob runs, web workers, retries, tags, JUnit and JSON output |
| Extensions | Entry-point drivers, step types, and lifecycle hooks |

### How it compares

| Tool | What it is | Relationship to TestBench |
| --- | --- | --- |
| Playwright | Browser automation engine | TestBench wraps it for YAML-driven web checks |
| Appium | Cross-platform UI automation framework | TestBench uses Appium for Android; it does not replace Appium |
| Cypress | Browser testing framework with its own runner and ecosystem | TestBench is a separate, multi-target YAML layer, not a Cypress alternative |

## Install

The recommended isolated install is `pipx install testbench`. Select optional
extras only when needed:

```sh
pip install testbench                 # core + web
pip install "testbench[mobile]"       # Android/Appium
pip install "testbench[game]"         # OpenCV and desktop capture/input
pip install "testbench[ai]"           # optional AI helpers and safe image processing
pip install "testbench[all]"
```

Contributors can install the development environment with
`python -m pip install -e ".[dev]"`. See the [installation guide](docs/installation.md)
for platform prerequisites.

## Write a test

One action per step. Sensitive values should use environment placeholders:

```yaml
name: Login works
target: web
variables: [LOGIN_PASSWORD]
config:
  browser: chromium
steps:
  - open: https://the-internet.herokuapp.com/login
  - type: {field: Username, value: tomsmith}
  - type: {field: Password, value: ${LOGIN_PASSWORD}}
  - click: Login
  - expect_visible: You logged into a secure area!
  - screenshot: after_login
```

See the [generated YAML reference](docs/yaml-reference.md) for all built-in
actions and [writing tests](docs/writing-tests.md) for CLI options.

The checked-in examples include
[`tests_examples/web_login.yaml`](tests_examples/web_login.yaml),
[`tests_examples/android_example.yaml`](tests_examples/android_example.yaml),
and [`tests_examples/game_example.yaml`](tests_examples/game_example.yaml).

## Use the CLI

```sh
testbench doctor
testbench run tests/ --workers 4 --retries 2 --tag smoke
testbench run "tests/**/web_*.yaml" --junit reports/junit.xml --json-results reports/results.json
testbench serve
```

The dashboard opens at <http://127.0.0.1:8000>. Run artifacts and SQLite history
are written under `runs/`; standalone reports are in `reports/<run_id>/`.
Configure `TESTBENCH_TESTS_DIR`, `TESTBENCH_RUNS_DIR`,
`TESTBENCH_REPORTS_DIR`, and `TESTBENCH_DATABASE_PATH` to use other local
project paths.

See the [CI example](docs/ci.md), [plugin guide](docs/plugins.md), and
[roadmap](docs/roadmap.md).

## Development and test coverage

Run the local checks with `python -m pytest` after installing the `dev` extra.
The current test suite reports **58% statement coverage**; the optional
mobile, game, recorder, and AI integration tests are skipped unless their
required local services or devices are available.

## API

- `GET /api/tests` — list YAML tests
- `POST /api/tests/{test_name}/run` — start a test
- `GET /api/runs/{run_id}` — poll live status or retrieve a saved run
- `GET /api/runs` — list recent run history
- `GET /api/runs/{run_id}/report` — serve its HTML report
- `GET /api/devices` — list connected devices and local AVDs
- `POST /api/devices/emulator/start` — start an AVD (`{"avd_name":"Pixel_API_35"}`)
- `GET /api/mobile/inspect` — inspect the Android Settings screen on a device
- `GET /api/game/screenshot` — capture the selected game surface
- `POST /api/game/templates` — crop and save an image template
- `GET /api/game/templates` — list saved templates
- `POST /api/game/templates/{name}/match` — test a template against the current screen

The CLI also provides `testbench devices`, `testbench mobile install`,
`testbench mobile start`, `testbench mobile stop`, and `testbench doctor
--mobile`. Mobile Inspector is available from the dashboard or at
`http://localhost:8000/mobile-inspector`.

## Mobile testing

TestBench currently supports Android emulators and authorized USB devices via
Appium 2 and UiAutomator2. iOS support is a documented TODO; no iOS driver is
included in this release.

### Prerequisites

1. Install Android Studio and, in **SDK Manager**, install Android SDK Platform-
   Tools, Android Emulator, and one Android SDK Platform. Create an AVD in
   **Device Manager** if using an emulator.
2. Install a **JDK 17 or newer** (a JRE alone is not sufficient).
3. Install Node.js LTS (npm is included).
4. Install TestBench's Appium server and Android driver as shown below.

On Windows, Android Studio normally installs the SDK at
`%LOCALAPPDATA%\Android\Sdk`. Configure the current and future terminals:

```powershell
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
setx ANDROID_HOME "$env:LOCALAPPDATA\Android\Sdk"
$env:Path += ";$env:ANDROID_HOME\platform-tools;$env:ANDROID_HOME\emulator"
```

On macOS, install Android Studio and configure the SDK:

```sh
brew install --cask android-studio temurin@17
export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```

On Debian/Ubuntu Linux, install the JDK and Node.js, install Android Studio from
the official Android developer site, then configure the SDK:

```sh
sudo apt update
sudo apt install -y openjdk-17-jdk nodejs npm
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```

Persist `ANDROID_HOME` and `PATH` in your shell profile. The SDK needs to contain
`platform-tools/adb` and `emulator/emulator`; use Android Studio SDK Manager to
install those components.

### First emulator run

1. Open Android Studio's **Device Manager**, create and boot an AVD.
2. In the TestBench project environment, check prerequisites and install
   Appium/UiAutomator2:

   ```sh
   testbench doctor --mobile
   testbench mobile install
   ```

3. Start the local Appium server and verify the emulator is authorized:

   ```sh
   testbench mobile start
   testbench devices
   ```

   For a USB device, enable Developer options and USB debugging, connect it,
   accept the device authorization prompt, then confirm `adb devices -l` lists
   its serial with status `device`.

4. Run the built-in Settings example:

   ```sh
   testbench run tests_examples/android_example.yaml
   ```

   The command prints the static report path. Stop the Appium server when done:

   ```sh
   testbench mobile stop
   ```

You can also start a named AVD using `testbench devices` to discover its name
and `POST /api/devices/emulator/start` with `{"avd_name":"<name>"}`. The
dashboard's **Mobile Inspector** page shows a live Settings screenshot and
hierarchy; click a highlighted element to copy its locator as a YAML step.

### Android YAML reference

```yaml
name: Settings display page
target: android
config:
  appium_url: http://127.0.0.1:4723
  device: auto                 # or an adb serial such as emulator-5554
  package: com.android.settings
  activity: .Settings
  reset: false                 # set true for a fresh APK install
  timeouts:
    implicit: 0
    explicit: 10
steps:
  - open: settings
  - expect_visible: Network & internet
  - scroll_to: Display
  - swipe: {direction: up}
  - press_key: back
  - set_orientation: landscape
  - screenshot: settings_screen
```

To install an APK, set `app: ./apps/demo.apk` instead of `package` and
`activity`. For an already-installed app, set `package` and optionally
`activity`. Config requires a valid Appium HTTP(S) URL and either an APK path or
an installed package. `timeouts.explicit` defaults to 10 seconds;
`timeouts.implicit` defaults to 0.

The shared actions (`open`, `click`, `type`, `wait`, `screenshot`,
`expect_visible`, `expect_text`) work on Android. Android also supports `swipe`
with `up|down|left|right`, `scroll_to`, `back`, `home`, `hide_keyboard`,
`press_key` with `enter|back|home`, and `set_orientation` with
`portrait|landscape`. Clicks can use semantic text or an explicit locator:

```yaml
- click: Login
- click: {id: "com.example.app:id/login_btn"}
- click: {xpath: "//android.widget.Button[@text='Login']"}
- click: {accessibility_id: login}
```

Android reports include device details, screen recording, logcat, and available
failure page-source XML. All are saved locally under `runs/` and copied into the
standalone report assets.

### Troubleshooting

- **`adb` not found:** install *Android SDK Platform-Tools* in Android Studio
  SDK Manager, set `ANDROID_HOME` to your SDK directory, add
  `$ANDROID_HOME/platform-tools` to `PATH`, open a new terminal, and run
  `adb devices -l`.
- **Emulator does not boot:** check that Android Emulator and the selected
  system image are installed, verify virtualization is enabled, start the AVD
  from Android Studio Device Manager, and inspect its output with
  `emulator -avd <avd-name> -verbose`.
- **USB device is `unauthorized` or missing:** accept the USB debugging prompt,
  use a data-capable cable, enable USB debugging, then run `adb kill-server`,
  `adb start-server`, and `adb devices -l`.
- **Appium session errors:** run `testbench doctor --mobile`; then check
  `testbench mobile start`, `appium driver list --installed`, and
  `testbench devices`. Ensure UiAutomator2 is installed, the configured
  `device` matches the adb serial (or is `auto`), and the package/activity or APK
  path is correct. The Appium server log is `runs/appium-server.log`; per-run
  logcat and failure XML are in that run's artifact folder.

## Game testing

The game driver uses OpenCV vision matching behind a shared `Surface` interface.
It can attach to a desktop window/monitor, an Android device through `adb`, or a
browser page through Playwright. No game automation engine or online service is
required. Launch desktop games yourself; browser games are opened by the driver.

**Only test games you own or have permission to test.** TestBench does not
bypass anti-cheat systems and is not intended for online-game botting or
automating other people's games.

### Quick start: included canvas demo

1. Install the project and Chromium as described above.
2. In a second terminal, serve the demo files locally:

   ```powershell
   python -m http.server 8001 --directory tests_examples
   ```

3. Run the included test:

   ```powershell
   testbench run tests_examples/game_example.yaml
   ```

   It locates the Start button from `tests_examples/game_demo/templates`,
   clicks it, waits for the canvas to change, checks the visible `Level 1`
   status, samples frame rate, and saves a screenshot and video. The report
   path is printed by the CLI.

The dashboard's **Game Template Maker** page can capture a current surface,
let you drag a crop, save a PNG, and copy a `click_image` YAML step. It also
shows existing templates and a **Test match now** confidence check. To use it,
start the dashboard with `testbench serve`, open
<http://localhost:8000/game-template-maker>, select a surface, fill in the
browser URL/window title/device when needed, and capture the screen. The default
template folder is `tests_examples/game_demo/templates`; override it with the
`TESTBENCH_GAME_TEMPLATES_DIR` environment variable or the page's folder field.

### Surfaces and configuration

```yaml
name: Start the browser game
target: game
config:
  surface: browser              # desktop | android | browser
  url: http://127.0.0.1:8001/game_demo/index.html
  templates_dir: tests_examples/game_demo/templates
  match_threshold: 0.85         # confidence from 0 to 1
  step_timeout: 15
  scale_range: [0.8, 1.2]
steps:
  - open: http://127.0.0.1:8001/game_demo/index.html
  - wait_for_image: start_button.png
  - click_image: {image: start_button.png, threshold: 0.9, region: [0, 0, 800, 600]}
  - wait_for_change: {region: [0, 0, 800, 600], timeout: 10}
  - expect_text: "Level 1"
  - measure_fps: {seconds: 5, min_fps: 30}
  - screenshot: after_start
```

- **Desktop:** `surface: desktop`, optionally set `window_title` to a distinctive
  part of the window title. Without it, TestBench captures the primary monitor.
  Input is sent through PyAutoGUI; optional window-title lookup uses
  `python -m pip install pygetwindow`.
- **Android:** `surface: android` and `device: auto` or an ADB serial such as
  `emulator-5554`. Install Android SDK Platform-Tools and authorize the device.
  This surface uses direct `adb` capture/input and does not create an Appium
  session.
- **Browser:** `surface: browser` and an absolute `url`. Playwright captures the
  browser viewport and sends mouse/keyboard input; it also exposes visible page
  text without requiring OCR.

The game step set includes `wait_for_image`, `click_image`, `expect_image`,
`expect_no_image`, OCR-based `click_text`/`expect_text`, `tap: {x, y}` (values
from 0 to 1 are relative coordinates), `drag: {from: [x,y], to: [x,y],
duration: 0.5}`, `press_key`, `hold_key`, `wait`, `wait_for_change`,
`expect_no_change`, `measure_fps`, and `screenshot`. Shared `click`,
`type`,
`expect_visible`, `expect_text`, and `close` are mapped to game actions; for
`expect_visible`, an image filename means image matching. `type` and
`click_text` on desktop/Android require an OCR engine (see below).

### Templates, OCR, and optional AI

Crop templates tightly around stable edges/text, avoid animated pixels, and
include enough visual detail for matching. `scale_range` enables multi-scale
matching for different resolutions. A failed match stores an annotated
screenshot with the best candidate confidence and a copy of the template under
the run's `debug/` artifacts; successful matches save an annotated click image.

For OCR, install Tesseract and the Python binding, or EasyOCR:

```powershell
python -m pip install pytesseract
winget install UB-Mannheim.TesseractOCR
# Alternative engine:
python -m pip install easyocr
```

The optional AI vision fallback is disabled by default. To enable it, install
`python -m pip install -e ".[ai]"`, set `OPENAI_API_KEY`, and set
`ai_fallback: true` in the game config. A `click_image` mapping must also
include a short `describe` field. When enabled and used, the screenshot is sent
to the configured OpenAI vision API; model output is validated and clamped to
the screen bounds and each use is noted in the report. Image matching and all
other local features work without this option or a key.

### Troubleshooting

- **Low match confidence:** recapture a tighter crop, include stable edges and
  text, avoid gradients/animation, or lower the per-step `threshold` only after
  checking the annotated best-candidate image.
- **Wrong scale/resolution:** widen `scale_range` modestly (for example
  `[0.7, 1.3]`) and make templates for the target display size. Avoid extremely
  wide ranges, which increase matching time.
- **OCR unavailable:** install Tesseract plus `pytesseract`, or install
  EasyOCR. Browser-visible text assertions do not need OCR.
- **Desktop window not found:** check the exact title text, ensure the game
  window is visible and not minimized, and install `pygetwindow` for title
  lookup. Without `window_title`, the primary monitor is captured.
- **Windows DPI scaling / offset clicks:** set the display scaling consistently
  while recording templates and running tests, use a window region instead of
  the entire desktop, and keep the game resolution fixed. Recreate templates
  after changing DPI or display scaling.
- **Android capture/input fails:** verify `adb devices -l` shows the target as
  `device`, not `unauthorized`; install Platform-Tools and set `ANDROID_HOME`.
- **Browser game does not open:** serve local files over HTTP and verify the
  URL loads in Chromium; do not rely on a remote paid service.

Game reports include surface/resolution, match confidences and annotations,
approximate FPS results, local recordings when available, and whether the AI
fallback was used.

## Recording tests

The Recorder captures user interactions and converts raw events into the same
YAML steps used by hand-authored tests. Password and other sensitive
web/mobile inputs are masked as `${ENV_VAR}` placeholders; provide those values
in the environment only when replaying. Screenshots may contain private data
and are written to local `runs/` artifacts. Record only apps and sites you own
or have permission to test.

### Quick start

Start the dashboard with `testbench serve`, open
<http://localhost:8000/recorder>, select a target, and press **Start Recording**.
The web recorder opens a headed browser; Android requires a running Appium
server and an authorized device; the game recorder supports desktop, Android,
and browser surfaces. Interact with the target, press **Stop**, review or edit
the ordered steps, and choose **Save as test**. The recorder also replays the
result once in the background and links to the report when ready.

The CLI records interactively and writes tests to `TESTBENCH_TESTS_DIR`
(default `tests_examples/`):

```sh
testbench record web https://example.test
testbench record android --device auto --package com.example.app
testbench record game --surface desktop --window "My Game"
```

For browser-based game capture use
`testbench record game --surface browser --url http://localhost:8000/game`;
Android game capture uses `--surface android --device emulator-5554`. Desktop
game recording listens for local mouse and keyboard events and saves a tight
crop around each click as `click_001.png`, `click_002.png`, and so on. Review
and rename templates in the Game Template Maker before relying on them.

### Locators and editing

Web locators prefer role/name, label, placeholder, visible text, test IDs, and
stable IDs before CSS or XPath. The recorder checks uniqueness at capture time.
Android interactions use accessibility descriptions, visible text, resource
IDs, and finally relative coordinate taps when the accessibility hierarchy
cannot identify a touched control. Screenshots and Android hierarchy XML are
stored alongside the recording.

The live Recorder page supports pause/resume, editing or inserting a step
mapping, deleting steps, and moving steps up or down. Use the page toolbar's
assertion controls to capture visibility, text, URL, or enabled-state
expectations; its screenshot and wait controls add explicit steps. Prefer state
assertions over fixed waits, and semantic locators over coordinates when
available.

### Export to pytest

Web YAML tests can be exported to readable pytest-playwright code with a small
Page Object helper:

```sh
testbench export tests_examples/web_login.yaml --format pytest-playwright
pytest tests_examples/web_login_playwright.py
```

Set any required `${ENV_VAR}` values in the environment before running the
exported test. Android and game tests are not supported by this exporter yet.

## AI assist

AI features are opt-in and are never required to run an existing test.
`testbench.toml` starts with `enabled = false`. Choose a provider and model
there, and keep credentials only in environment variables or the gitignored
project `.env` file:

```toml
[ai]
enabled = true
provider = "ollama"
model = "llama3.1"
vision_model = "llava"
max_calls_per_run = 20
redact_secrets = true
healing = "suggest"
consented_providers = []
```

### Free provider setup

- **Ollama (local/private):** install Ollama, then run
  `ollama pull llama3.1` and `ollama pull llava` for vision tasks. The default
  endpoint is `http://localhost:11434`; no API key or internet call is needed
  after model download.
- **Gemini free tier:** select `provider = "gemini"`, set `GEMINI_API_KEY` in
  the process environment or `.env`, and choose a model available to your
  account.
- **OpenAI-compatible endpoints:** select `provider = "openai_compatible"`,
  set `base_url` and `OPENAI_API_KEY` (or configure `api_key_env`) for a
  compatible service such as Groq, OpenRouter, or a local LM Studio endpoint.
- **Anthropic:** select `provider = "anthropic"` and set `ANTHROPIC_API_KEY`.
  This provider may incur charges; verify its current plan and pricing.

Check connectivity and model availability with `testbench ai doctor`. The
dashboard's **AI Test Writer** and `testbench ai generate` produce a draft
using the Pydantic-derived step schema, then validate it. Web generation uses
a bounded accessibility/text/form snapshot from the configured URL. The
optional Iterate checkbox runs a draft and can regenerate it from failures
for at most three rounds. Review the displayed diff before saving.

```sh
testbench ai doctor
testbench ai generate "Open the login page and check the Email field" \
  --target web --url https://example.test/login --save login-smoke
testbench ai suggest tests_examples/web_login.yaml
testbench ai explain <run_id>
```

Before any provider receives page content, screenshots, or diagnostics, grant
one-time per-provider consent in the AI Test Writer or with
`testbench ai consent --provider ollama`. The notice names the provider; use
Ollama for fully local processing. Consent is stored in `testbench.toml`, and
revocation is available in the dashboard. API keys are read only from the
environment or `.env`; they are not written to tests, reports, or logs.

Before provider calls, TestBench redacts password/secret fields, credentials,
emails, card-like numbers, cookies, and authorization headers. Calls are
bounded by the per-run call limit and prompt size, with a SQLite cache for
identical requests. Usage records contain provider/model, estimated tokens,
purpose, redaction count, and cache hits—not prompt text by default. Setting
`debug_log = true` explicitly stores prompt text in the local gitignored
`runs/` database; keep it off when prompts may contain private content.
Screenshot sharing is fail-closed: text regions are OCR-masked first, and
screenshots are withheld if local Tesseract OCR is unavailable or fails.

### Locator healing and failure analysis

Healing modes can be set globally under `[ai].healing` or overridden per test
with `config.healing`:

- `off`: no locator repair.
- `suggest` (default): deterministic fuzzy/label alternatives are tried first;
  if AI is enabled and consented, it may suggest a validated replacement.
  The original step still fails until a user reviews and applies the fix.
- `auto`: only non-assertion steps may continue with a replacement that matches
  exactly one visible, enabled element of a compatible role/type. The report
  marks the step **HEALED** in yellow and records both locators and confidence.

Assertions are never auto-healed because changing an assertion target could
hide a real application defect. The dashboard keeps healing history; reports
offer **Apply fix**, and `testbench heal apply <run_id>` applies previously
accepted fixes to the source YAML. Review every proposed change.

Failed runs always include a basic rule-based explanation, even with AI off.
The **Explain failure** action and `testbench ai explain <run_id>` can request
an additional model explanation after consent. It is clearly labeled “AI
suggestion, may be wrong.” Failure screenshots are not sent unless a caller
has already safely redacted them; page source, console/network details, and
Android logcat are redacted before use.

**AI can be wrong.** Generated steps may use incorrect labels, miss state
transitions, or misunderstand the application. Always validate, dry-run, and
review the YAML/report. Generated and healed URLs are restricted to domains
already configured for the test. AI output is restricted to the TestBench
step schema; page text is treated as untrusted data and never as instructions.
AI scenario suggestions are drafts; accept or reject each one. Fake form data
uses seeded Faker values locally and does not use a model.

## Develop

```powershell
python -m pytest
```

## Roadmap

- Recording user interactions into editable YAML steps
- More resilient locator and mobile gesture support
- Additional game surfaces and robust recording options
- Test suites, environment variables/secrets, and parallel runs
- Optional local-model AI assistance
