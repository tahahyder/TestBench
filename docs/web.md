# Web testing

The web driver uses Playwright and prefers accessible roles, labels,
placeholders, visible text, and then CSS locators. Install a browser with
`playwright install chromium`.

```yaml
target: web
config:
  browser: chromium
steps:
  - open: https://example.com
  - expect_visible: Example Domain
  - screenshot: example
```

Traces, recordings, screenshots, and a standalone report are saved under the
local run and report folders.
