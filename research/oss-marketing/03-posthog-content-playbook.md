# PostHog content-marketing playbook (open-source dev-tool)

**Sources:**
- https://1984.vc/docs/founders-handbook/eng/open-source-playbook-posthog/
- https://posthog.com/handbook/growth/marketing/blog
- https://www.battery.com/blog/the-genius-of-posthog-marketing/
**Type:** Vendor handbook + investor case studies
**Date accessed:** 2026-05-16

## Key insights

- **The "X best open source [category] tools" article is the unlock.** PostHog's single most successful piece ever was *"The 12 best open source analytics tools."* It works because: (a) it owns a high-intent SEO term, (b) it positions PostHog as a fair-broker reviewer of the category, (c) it self-includes credibly. This is the canonical OSS-tool content move.
- **Short, targeted integration pieces** (one tool x one stack) outperform long generic tutorials.
- **Controversial-stance posts perform best**: opinions about how engineering teams *should* operate, not feature posts.
- **97% of early growth was word-of-mouth.** Marketing job is to *give people something to say*, not push.
- **Transparency-as-marketing**: public company handbook, public roadmap, public revenue/churn; turns the org into content.
- Newsletter ("Product for Engineers") used to build sustained relationship, not capture.

## Applicability to archy

- **Write "The N best open-source Python architecture tools."** Cover pydeps, snakefood, import-linter, pylint's import-graph, archcheck, dependency-cruiser (cross-language for context), Sourcetrail (RIP), and archy. Be honest about where each wins. This becomes the SEO + HN-friendly canonical piece. Estimated win condition: top 3 result for "python architecture analysis" and "python import graph tool."
- **Write integration cookbook posts**, one per pairing, all short:
  - "archy + Claude Code: structural guardrails for agentic coding"
  - "archy + Cursor"
  - "archy + import-linter: when to use which"
  - "archy in pre-commit"
  - "archy in GitHub Actions"
- **Opinion piece** with archy's actual thesis: "AI coding agents will rot your architecture if you don't measure it" or "Your codebase needs a sensor, not a linter." Cite the Navigation Paradox / LocAgent papers already in `docs/RESEARCH_METRICS.md`. This is the post that gets reposted.
- **Public handbook for archy** is overkill at current scale. But: a public `docs/ROADMAP.md` and public benchmark results (already in `docs/CASE_STUDIES.md`) play the same role.
- Don't start a newsletter yet; premature for a club-type project.
