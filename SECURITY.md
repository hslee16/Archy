# Security Policy

## Supported versions

archy is pre-1.0 and ships frequently. Security fixes land on the latest
released version; please upgrade to the most recent release on PyPI before
reporting (`uv tool upgrade archy` or `pip install -U archy`).

| Version | Supported |
|---|---|
| Latest release | yes |
| Older releases | no (upgrade to latest) |

## Reporting a vulnerability

Please report security issues **privately**, not as a public issue or PR.

- Preferred: open a private advisory via GitHub's "Report a vulnerability"
  button on the [Security tab](https://github.com/hslee16/archy/security/advisories/new).
- This keeps the report confidential until a fix is available.

What to expect:

- **Acknowledgement within 14 days** of your report.
- A fix or documented mitigation targeted **within 90 days** of
  acknowledgement, coordinated with you on disclosure timing.

archy is maintained by a single maintainer on a best-effort basis; these are
targets, not contractual SLAs.

## Threat model (what archy does and does not do)

- archy **statically parses** Python source with tree-sitter. It does **not
  import or execute** the code it analyzes, so pointing archy at an untrusted
  repository does not run that repository's code.
- The MCP server (`archy mcp`) runs **locally over stdio**; it opens no network
  listener and makes no outbound network calls.
- `archy install` / `archy uninstall` write agent config files under the user's
  home or a project directory (see [`docs/INSTALL.md`](docs/INSTALL.md)); they
  perform no other side effects and never phone home.
- The persistent cache (`.archy/index.db`) and history (`.archy/`) are local
  files; deleting them is always safe.

The realistic security surface is therefore the usual supply-chain one (archy's
own dependencies). Reports about a vulnerable dependency are in scope.
