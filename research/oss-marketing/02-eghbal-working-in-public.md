# Nadia Eghbal: Working in Public (2020), project-type framework

**Source:** Book; summary via https://project-types.github.io/ and https://notes.aquiles.me/essays/notes_on_working_in_public_-_nadia_eghbal/
**Type:** Book (canonical text on modern OSS maintainer economics)
**Date accessed:** 2026-05-16

## Key insights

Eghbal classifies OSS projects on two axes: user growth and contributor growth.

| | low user growth | high user growth |
|---|---|---|
| **low contributor growth** | toy | stadium |
| **high contributor growth** | club | federation |

- **Toy:** one-off, no expectation others use it.
- **Club:** cozy niche, users *become* contributors (astropy, scientific tools).
- **Stadium:** many users, few maintainers; the dominant modern shape (most npm libs, most viral GitHub repos). Attention asymmetry causes maintainer burnout.
- **Federation:** rare; high on both axes (Linux, Rust). Requires governance investment.

- "Marketing" in OSS doesn't map cleanly to commercial GTM because **attention itself is a tax on maintainers**, not pure upside. Growing users *without* growing contributors moves you into stadium territory, where every star is a future support cost.
- Distribution platforms (GitHub, package registries) function as social media: discovery is driven by algorithmic feeds, social proof (stars), and creator-following.
- Maintainers should explicitly choose which quadrant they're targeting and shape contribution norms accordingly (issue templates, "we don't accept X" notices, scope discipline).

## Applicability to archy

- archy is almost certainly best modelled as a **club** (Python infra/tooling niche, contributors plausibly drawn from users who use it daily); not a stadium. Promotion should target *depth in niche*, not viral reach.
- Specifically: don't optimize for HN front page; optimize for repeated visibility inside the AI-coding-tools + Python-architecture niches (Cursor/Claude Code/Cline forums, r/Python, Python Bytes podcast, Talk Python newsletter, Architecture Weekly, MCP directories).
- Scope discipline as marketing: the README's "Python only / not multi-language" stance is *correct* per Eghbal; it filters for the right kind of user and pre-empts a class of support load. Keep it.
- Attention asymmetry implies we should ship **self-serve onboarding artifacts** (one-command install, `archy mcp` stanza, an `archy --diagnose` mode) *before* doing any push that could spike issue volume.
