# Getting started

```sh
python -m pipx install testbench
testbench init demo
cd demo
testbench doctor
testbench run tests/sample_web.yaml
testbench serve
```

The starter test opens a public demo login page and checks that its heading is
visible. TestBench stores state locally in the project directory.

The browser example uses a public demo site. For your own tests, use only
applications and sites you own or are authorized to test.
