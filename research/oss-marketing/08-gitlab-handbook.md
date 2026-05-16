# GitLab handbook: transparent marketing for open source

**Source:** https://handbook.gitlab.com/handbook/marketing/ and https://handbook.gitlab.com/handbook/engineering/open-source/growth-strategy/
**Type:** Public company handbook (canonical "transparency as marketing" exemplar)
**Date accessed:** 2026-05-16

## Key insights

- **The handbook itself is the marketing asset.** Publishing process, strategy, even compensation makes the company *itself* into a content surface that is continuously indexed and shared.
- **Transparency core claim:** "Creates more value than it captures." The hypothesis is that giving away strategy and process attracts (a) users who agree with the values, (b) faster outside feedback, (c) easier collaboration with non-employees.
- **Open social media strategy:** social posts, drafts, calendars are public. Other people can borrow templates. Reduces marketing-team isolation and turns followers into co-creators.
- **Single-source-of-truth content ops:** every blog/social/doc cross-references handbook canonical pages; no information lives only in someone's head.
- The model only works if the org is OK with *being seen losing*; half-finished plans, walked-back decisions, public retros. That cost is real.

## Applicability to archy

- archy is a single-person project so a "handbook" is overkill, but the principle scales down: **make the decision artifacts public** in `docs/`.
  - `docs/LEARNINGS.md` already does this (good).
  - `docs/RESEARCH_METRICS.md` already does this (good).
  - **What's missing:** a public `docs/ROADMAP.md` with what's next + why, and explicit "deferred" / "rejected" sections. The roadmap section in README is short; promote it to its own file and treat it as a content asset.
  - The `research/oss-marketing/` directory we're building right now; if made public after this synthesis, *is* the kind of artifact that earns transparency credit. Decide explicitly whether it stays private or ships in the repo.
- **Cross-referencing.** Every external post about archy should link the canonical doc (e.g., a tweet about hotspots links `docs/SCORING.md` not just the README). Builds the SEO graph and gives the canonical doc its rightful authority.
- **Public retros after each release.** Even one paragraph in the release notes: "what we tried and walked back this cycle." Cheap, signals seriousness.
- **One concrete decision to make:** is `research/oss-marketing/` going to be committed to the repo or stay in a private dir? If committed, it earns the transparency dividend immediately *and* signals to other OSS maintainers that archy thinks systematically. If kept private, it's just a planning doc. Recommendation: commit it once SYNTHESIS hits 15 sources and is reviewed.
