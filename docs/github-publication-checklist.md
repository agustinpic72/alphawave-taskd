# GitHub Publication Checklist

Complete this checklist on the exact release-candidate commit. Keep the canonical repository private until all blocking items pass.

## Repository Content

- [ ] The history is a new sanitized history using the maintainer's GitHub noreply email.
- [ ] No secret, private taxonomy, personal data, local path, internal hostname, runtime data, or private source history is present.
- [ ] `LICENSE`, `README.md`, `SECURITY.md`, `SUPPORT.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, and `ROADMAP.md` render correctly.
- [ ] Asset provenance covers every SVG, PNG, icon, and screenshot.
- [ ] Screenshots contain synthetic `[DEMO]` content only.
- [ ] Clone and Quick Start URLs use `agustinpic72/alphawave-taskd`.
- [ ] Product naming consistently uses `AlphaWave TaskD`.

## Exact-Commit Gates

- [ ] Publication audit and its synthetic regression pass.
- [ ] Gitleaks passes on the tree and reachable history.
- [ ] Backend installation, dependency check, and full test suite pass.
- [ ] Frontend `npm ci`, dependency audits, lint, build, and browser smoke pass.
- [ ] Compose configuration/build and runtime/persistence smoke pass.
- [ ] Required GitHub Actions checks are green on the exact candidate commit.

## Repository Settings

- [ ] Description and topics match the local-first scope.
- [ ] Vulnerability alerts and automated security fixes are enabled where supported.
- [ ] Dependabot and CodeQL configurations have been reviewed; do not call them active until a remote run confirms it.
- [ ] `main` rejects force pushes and deletion and uses appropriate review/check protection.
- [ ] Wiki and projects are disabled unless intentionally maintained.

## Release

- [ ] The repository is publicly readable in a signed-out browser.
- [ ] [Anonymous review](anonymous-review-checklist.md) passes.
- [ ] Tag `v0.1.0` targets the verified commit.
- [ ] Release notes cover installation, optional integrations, security boundaries, and local-first limitations.
- [ ] No unreproducible binary is attached.
