# CNCF project maturity ladder (Sandbox / Incubation / Graduated)

**Source:** https://github.com/cncf/toc/blob/main/process/graduation_criteria.md
**Type:** Foundation governance criteria (canonical OSS project-maturity scaffold)
**Date accessed:** 2026-05-16

## Key insights

Even outside CNCF, the criteria function as a checklist for "what makes an OSS project look serious." Used as a marketing scaffold:

**Sandbox-level signals:**
- README, LICENSE, code of conduct, contributor guide present.
- Public issue tracker, public roadmap, public discussion forum.

**Incubation-level signals:**
- **3+ independent production users** documented publicly (`ADOPTERS.md`).
- Healthy commit cadence from multiple contributors.
- `MAINTAINERS.md` with named humans and explicit on/off-boarding criteria.
- Annual maintainer audit.

**Graduated-level signals:**
- Committers from 2+ organizations.
- OpenSSF Best Practices badge (formerly CII).
- Third-party security audit, published.
- Explicit `GOVERNANCE.md` and committer lifecycle docs.

## Applicability to archy

archy is not joining CNCF (not cloud-native scope). But the checklist is a free credibility scaffold to copy:

- ✅ README, ✅ LICENSE (MIT), ✅ CONTRIBUTING.md, ❌ CODE_OF_CONDUCT.md, ✅ roadmap (in README; should be promoted to `docs/ROADMAP.md` per item 23).
- ❌ `ADOPTERS.md`; even an empty file with a "PR welcome" note is a signal. Adding real adopters as they appear is the #1 credibility ladder rung. Tie to P6 item 17 (in-the-wild section).
- ❌ `MAINTAINERS.md`; for a solo project, a one-line "Sole maintainer: [name + contact]" is sufficient; signals legibility per Levine.
- ❌ OpenSSF Best Practices badge; straightforward self-assessment that takes ~2 hours and yields a badge. High signal-to-effort ratio. (https://www.bestpractices.dev/)
- ❌ `GOVERNANCE.md`; overkill for solo project; revisit when there are 2+ committers.

**Action: ship a CNCF-style "credibility kit" PR.** Adds CODE_OF_CONDUCT.md (Contributor Covenant), ADOPTERS.md (empty starter), MAINTAINERS.md (one line), GOVERNANCE.md (simple BDFL note), OpenSSF badge. Half a day of work; permanent credibility signal visible to anyone evaluating archy in 30 seconds.
