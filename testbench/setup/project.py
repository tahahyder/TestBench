"""First-run project scaffolding."""

from pathlib import Path

_STARTER_FILES = {
    "testbench.toml": """[ai]
enabled = false
provider = "ollama"
model = "llama3.1"
vision_model = "llava"
max_calls_per_run = 20
redact_secrets = true
""",
    ".gitignore": """.env
.venv/
__pycache__/
.pytest_cache/
runs/
reports/
""",
    "tests/sample_web.yaml": """name: Example login page loads
target: web
config:
  browser: chromium
steps:
  - open: https://the-internet.herokuapp.com/login
  - expect_visible: Login Page
""",
    "README.md": """# My TestBench project

Local test automation using readable YAML tests.

## Run the sample

```sh
testbench doctor
testbench run tests/sample_web.yaml
testbench serve
```

TestBench stores run artifacts in `runs/` and standalone reports in `reports/`.
""",
}


def initialize_project(project_dir: Path) -> list[str]:
    """Create starter directories and files without overwriting user content."""
    destination = project_dir.expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"Project path is not a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    for directory in ("tests", "templates", "apps"):
        path = destination / directory
        if not path.exists():
            path.mkdir()
            created.append(directory + "/")
        elif not path.is_dir():
            raise ValueError(f"Cannot create starter directory; file exists: {path}")

    for name, content in _STARTER_FILES.items():
        path = destination / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        created.append(name)
    return created
