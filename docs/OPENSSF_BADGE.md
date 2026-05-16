# OpenSSF Best Practices badge: pre-assessment

Draft answers and evidence pointers for the [Best Practices badge self-assessment](https://www.bestpractices.dev/) at the **passing** level. Goal: submit once, get the badge, embed in README.

## What the badge is

The OpenSSF Best Practices badge (formerly CII Best Practices) is a free, self-certified credibility signal that the project follows a baseline of OSS hygiene: a public site, clear license, reproducible build, security disclosure path, automated tests, etc. ~80 yes/no questions. Each level (passing, silver, gold) raises the bar. Most established OSS projects target *passing* and stop there.

Why archy should bother: per the Evil Martians dev-tool teardown and the CNCF graduation checklist, recognized credibility badges are a fast way to make a project read as "serious" in a 30-second evaluation. Cost: ~2 hours of form-filling once; the badge stays valid as long as the answers remain true.

## Submission path

1. Sign in at https://www.bestpractices.dev/en with the maintainer's GitHub account.
2. Click "Get your project's badge", URL: `https://github.com/hslee16/Archy`.
3. Fill in answers using the draft below. Each question has Met / Unmet / Unknown / N/A.
4. Submit. Embed the resulting Markdown badge snippet at the top of `README.md`, next to the existing PyPI / CI badges.

## Draft answers (passing level)

For each section: status, evidence, and any TODO before submitting.

### Basics

- **Project website**: https://github.com/hslee16/Archy
- **Project description**: Use the short description from `docs/MCP_DIRECTORIES.md`.
- **License**: MIT (file: `LICENSE`).
- **Documentation basics**: README + `docs/` directory with 12+ files. **Met.**
- **Interact**: Public issue tracker on GitHub. **Met.**
- **Contribution**: `CONTRIBUTING.md` at repo root. **Met.**

### Change control

- **Public version-controlled source repository**: yes, on GitHub. **Met.**
- **Distributed version control**: git. **Met.**
- **Unique version numbering**: SemVer-ish (0.X.Y), tags match PyPI releases. **Met.**
- **Release notes**: GitHub releases per tag. **Met (verify each release has notes; some early ones may not).**

### Reporting

- **Bug-reporting process**: GitHub issues, documented in `CONTRIBUTING.md`. **Met.**
- **Vulnerability reporting**: **TODO.** Need a `SECURITY.md` file at repo root with a private disclosure email (use the maintainer's GitHub-noreply or a dedicated alias). Until then this is Unmet.
- **Vulnerability response time**: commit to acknowledging within 14 days. Document in `SECURITY.md`.

### Quality

- **Working build system**: yes, `uv build`. **Met.**
- **Automated test suite**: yes, `pytest` in CI. **Met.**
- **New tests for new functionality**: yes, every PR adds tests for new behavior. **Met.**
- **Warning flags**: ruff + ty in CI. **Met.**

### Security

- **Secure development knowledge**: Met by maintainer self-attestation.
- **Use basic good cryptographic practices**: N/A (archy does not handle credentials, network requests, or stored secrets).
- **Secured delivery against MITM**: PyPI delivers over HTTPS; GitHub Actions uses HTTPS. **Met.**
- **Publicly known vulnerabilities fixed**: no known unfixed vulns. **Met.**
- **Other security issues**: nothing known.

### Analysis

- **Static analysis**: ruff (lint) + ty (types) on every PR. **Met.**
- **Dynamic analysis**: pytest with full code coverage on every PR. **Met.**

## Pre-submission TODO

Before clicking submit at bestpractices.dev:

1. **Create `SECURITY.md`** at the repo root with a disclosure email (or "file a private GitHub security advisory"), expected acknowledgement window (14 days), and expected fix-or-mitigation window (90 days). One short file; ~10 minutes. Without this, the vulnerability-reporting question is Unmet and you cannot reach passing.
2. **Verify every existing GitHub release has notes.** Backfill any that are missing a body; 1 line per release is fine.
3. **Add a one-line "Reporting security issues"** pointer in `README.md` linking to `SECURITY.md`.

## Embedding the badge

Once awarded, add to `README.md` immediately under the existing badge row:

```markdown
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/PROJECT_ID/badge)](https://www.bestpractices.dev/projects/PROJECT_ID)
```

Replace `PROJECT_ID` with the integer the site assigns on submission.
