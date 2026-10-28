"""Minimal opt-in Slack completion notifier for TestBench."""

import json
import os
from urllib.request import Request, urlopen


class SlackNotifier:
    """Send only the test name and final status to a user-provided webhook."""

    def after_run(self, result) -> None:
        webhook = os.environ.get("TESTBENCH_SLACK_WEBHOOK", "").strip()
        if not webhook:
            return
        payload = json.dumps(
            {"text": f"TestBench: {result.test_name} — {result.status}"}
        ).encode("utf-8")
        request = Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Slack webhook returned HTTP {response.status}.")

    def before_run(self, result) -> None:
        return None

    def after_step(self, result, step) -> None:
        return None

    def on_failure(self, result, step) -> None:
        return None
