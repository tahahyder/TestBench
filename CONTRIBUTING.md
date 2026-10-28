# Contributing

## Development setup

Use Python 3.11 or newer:

```sh
python -m venv .venv
# Activate .venv for your platform.
python -m pip install -e ".[dev,docs]"
python -m playwright install chromium
```

Run checks before opening a pull request:

```sh
ruff check testbench tests
ruff format --check testbench tests
mypy testbench/core
pytest
mkdocs build --strict
```

## Code and tests

Keep changes focused, type annotated, and covered by tests. Unit tests must not
make real network calls. Mark device, game, recorder, and AI integrations with
their documented pytest markers.

To add a driver, implement `testbench.api.Driver` and register a
`testbench.drivers` entry point. To add a step, provide a Pydantic `model`,
`execute(driver, value)`, and a `testbench.steps` entry point. See
`examples/plugin-slack-notifier/` for the hook plugin shape.

Do not include credentials, user data, screenshots with secrets, or
machine-specific paths in pull requests.
