# Game testing

Install `pip install "testbench[game]"`. Game steps use image templates,
coordinates, OCR (when Tesseract is installed), and screen-change checks. Choose
`desktop`, `android`, or `browser` as the configured surface.

Crop templates tightly, avoid animated pixels, and keep templates for common
resolutions. Test only games you own or have permission to test. TestBench does
not provide anti-cheat bypass or online-game botting features.

Desktop game testing requires a graphical session. Docker is not a suitable
environment for desktop input or Android device access.
