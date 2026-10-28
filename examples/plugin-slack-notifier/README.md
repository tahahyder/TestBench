# Example Slack notifier plugin

This example demonstrates the stable `testbench.hooks` entry-point group. It
does not contact Slack unless you explicitly set `TESTBENCH_SLACK_WEBHOOK`.
Only the test name and final status are sent; do not put secrets in test names.

Install TestBench and this example in the same environment:

```sh
python -m pip install testbench
python -m pip install .
```

Set `TESTBENCH_SLACK_WEBHOOK` only if you intend to send notifications. Hook
errors are recorded in run metadata as `plugin_errors` and do not silently
change test results.

The public API is versioned as `testbench.api.API_VERSION == "1.0"`. Compatible
1.x releases preserve the documented driver, step-plugin, and hook contracts.
