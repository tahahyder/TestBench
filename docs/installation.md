# Installation

Python 3.11 or newer is required. The recommended isolated install is:

```sh
pipx install testbench
playwright install chromium
```

Optional capabilities:

```sh
pip install "testbench[mobile]"  # Android / Appium client
pip install "testbench[game]"    # OpenCV and desktop capture/input
pip install "testbench[ai]"      # Faker and image redaction support
pip install "testbench[all]"
```

Game OCR additionally requires a local Tesseract executable. Docker is intended
for web testing only; mobile and desktop game testing need access to local
devices, input, and display surfaces.
