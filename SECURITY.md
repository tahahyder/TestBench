# Security policy

TestBench is intended for local testing of applications and sites you own or
are authorized to test. The dashboard binds to `127.0.0.1` by default. Remote
binding requires an explicit access token; do not expose it on an untrusted
network.

AI features are off by default. Page data and screenshots are sent to a
third-party provider only after the user enables AI and records provider
consent. Local Ollama is available for local processing. TestBench redacts
recognized secrets and withholds screenshots if safe OCR redaction cannot run,
but redaction is not infallible. Review data before sharing.

To report a vulnerability, do not open a public issue with exploit details.
Use GitHub's private vulnerability reporting for this repository, or contact
the maintainers privately through the security contact listed in the GitHub
repository settings. Include affected versions, impact, and reproduction
details. The maintainers will acknowledge valid reports and coordinate a fix
and disclosure timeline.
