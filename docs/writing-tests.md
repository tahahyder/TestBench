# Writing tests

Each YAML step is a mapping with exactly one action. A test has `name`,
`target`, optional `config`, optional environment-variable `variables`,
optional `tags`, and `steps`.

```yaml
name: Login page is available
target: web
tags: [smoke]
steps:
  - open: https://the-internet.herokuapp.com/login
  - expect_visible: Login Page
```

Run one file, a folder, or a glob:

```sh
testbench run tests/login.yaml
testbench run tests/ --workers 4 --retries 2 --tag smoke
testbench run "tests/**/web_*.yaml" --junit reports/junit.xml --json-results reports/results.json
```

Use `${VARIABLE}` placeholders and declare variable names in the top-level
`variables` list for secrets. The runner reads values from the process
environment and does not print step values to its run log.
