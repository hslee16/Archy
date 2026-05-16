# Karl Fogel: Producing Open Source Software, Ch. on Publicity

**Source:** https://producingoss.com/en/publicity.html
**Type:** Book chapter (canonical OSS-governance text)
**Date accessed:** 2026-05-16

## Key insights

- **Multi-channel synchronized launches.** Homepage, release-notes section, mailing list, social, forums fire together so users don't see contradictions.
- **Quotable kernel.** Write announcements so the first paragraph contains the two critical facts; assume it will be partially quoted everywhere.
- **Tiered channels.** Reserve the announcement list/RSS for genuinely big events (releases, CVEs, direction shifts). Dilution kills trust.
- **Canonical artifact per announcement.** Each release/post should be its own permanently-linkable URL.
- **Word-of-mouth is the dominant distribution.** Construct even minor news to travel accurately when forwarded.
- **CVE / CVSS standards** signal professionalism for security disclosures.
- **Don't disclose bugs before a fix is shipped.** Bug + fix land in the same announcement.

## Applicability to archy

- Today the README is the only "announcement surface." Need a `CHANGELOG.md`-driven release notes page + a dedicated `docs/RELEASES/` per-version permalink so each release is a quotable artifact.
- Every release should land with: (1) PyPI publish, (2) GitHub release notes, (3) one social post (X / Bluesky / LinkedIn / r/Python / HN if substantive), (4) one short note in any MCP-server directory we're listed in (Glama, Smithery). They must fire within a small window with identical headline copy.
- Each release post needs a "quotable kernel": one-sentence what-shipped + one-sentence why-it-matters. Example for v0.18: "archy hotspots ranks Python modules by `cyclomatic_complexity x git churn` in one pass; find the file that costs you most before refactoring."
- A "major" tier should be reserved for: 1.0, new MCP tool with novel capability, integration with a notable agent platform. Patch releases stay quiet on the firehose channels.
- Adopt CVE numbering if/when archy gets a security-relevant report (low priority until we have one).

## Open questions

- Do we have a dedicated mailing list / changelog feed? README links to GH releases only.
- Is the version tagging story tight enough for monthly cadence? (Last 5 commits are version bumps; cadence looks high; risk of dilution per Fogel.)
