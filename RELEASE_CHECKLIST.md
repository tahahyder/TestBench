# Release checklist

The 0.1.0 source and packaging checks are prepared. These steps require
maintainer accounts and cannot be completed locally:

- [ ] Create the `testbench` project on PyPI or claim the existing name.
- [ ] Configure PyPI Trusted Publishing for the repository and `release.yml`
  workflow environment; do not create a long-lived API token.
- [ ] Enable GitHub Pages with the GitHub Actions source and allow the docs
  deployment workflow.
- [ ] Review the generated wheel/sdist, dependency licenses, and audit output.
- [ ] Run the fresh-virtual-environment install checklist in the release
  workflow on all supported platforms.
- [ ] Create and push the `v0.1.0` tag after the release commit is approved.
- [ ] Verify the PyPI release, GitHub Release notes, docs site, and published
  install from a separate machine.
