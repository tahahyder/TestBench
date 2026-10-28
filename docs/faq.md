# FAQ and troubleshooting

## Chromium is missing

Run `python -m playwright install chromium` and then `testbench doctor`.

## The dashboard does not find my tests

Run `testbench init` from the project directory or set
`TESTBENCH_TESTS_DIR` to the directory containing YAML test files.

## Why are AI features unavailable?

AI is off by default. Configure a provider, enable AI, run
`testbench ai consent <provider>`, and check availability with
`testbench ai doctor`.

## Can I expose the dashboard to a network?

It binds to loopback by default. Public binding requires `testbench serve
--host 0.0.0.0 --token <secret>`; use a trusted network and protect the token.
