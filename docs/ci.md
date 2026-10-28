# CI usage

The CLI returns `0` when all selected tests pass, `1` for test failures, and `2`
for setup or configuration errors.

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
- run: python -m pip install "testbench"
- run: python -m playwright install --with-deps chromium
- run: testbench run tests/ --junit reports/junit.xml --json-results reports/results.json
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: testbench-reports
    path: reports/
```

Use hosted runners for web tests. Android and desktop game runs need a runner
with a local emulator/device or graphical session.
