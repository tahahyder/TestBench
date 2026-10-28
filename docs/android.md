# Android testing

Install the extra with `pip install "testbench[mobile]"`, then install Android
Studio, Platform-Tools, JDK 17+, Node.js, Appium 2, and UiAutomator2. Run
`testbench doctor` for platform-specific installation commands.

```yaml
name: Settings opens
target: android
config:
  device: auto
  package: com.android.settings
  reset: false
steps:
  - click: {text: Network & internet}
  - expect_visible: Internet
```

Mobile runs remain serial per device. iOS is not currently supported.
