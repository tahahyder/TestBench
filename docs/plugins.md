# Plugins

Plugins are Python distributions that register entry points:

- `testbench.drivers`: entry-point name is the YAML target; the loaded class
  accepts `config` and `artifact_dir`.
- `testbench.steps`: entry-point name is the action; the loaded plugin exposes
  a Pydantic `model` and `execute(driver, value)`.
- `testbench.hooks`: loaded objects may implement `before_run`, `after_step`,
  `on_failure`, and `after_run`.

The stable public surface is `testbench.api` with
`testbench.api.API_VERSION == "1.0"`. Compatible 1.x releases preserve the documented contracts; breaking changes
require a major version. Hook failures are recorded in run metadata as
`plugin_errors`. A sample notifier is available under
`examples/plugin-slack-notifier/`.
