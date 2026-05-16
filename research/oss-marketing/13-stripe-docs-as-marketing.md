# Stripe: documentation as the primary marketing channel

**Sources:**
- https://apidog.com/blog/stripe-docs/
- https://everydeveloper.com/stripe-greatest-success/
- https://www.moesif.com/blog/best-practices/api-product-management/the-stripe-developer-experience-and-docs-teardown/
**Type:** Industry teardowns of the canonical developer-first marketing case
**Date accessed:** 2026-05-16

## Key insights

- **Docs *are* the conversion funnel.** Every doc page is designed to move a reader closer to first integration. "Time to first successful API call" is the KPI.
- **Three-column layout** (nav / prose / live code) became the industry default after Stripe. Code panel = always-visible runnable snippet.
- **Live execution in docs** (Stripe Shell). Reduces "do I need to set up my whole project to try this?" friction.
- **Marketing-to-experts.** No marketing-fluff language; assumes the reader knows what they're doing and respects their time.
- **Documentation is part of engineering ladders.** Quality of docs is something engineers are evaluated on, not separated to a docs team.
- **SEO-as-distribution.** Each doc page is a landing page for a specific developer search. "Stripe payment intent api" → docs page → integration.

## Applicability to archy

- **archy currently has good docs** (`SCORING.md`, `LEARNINGS.md`, `RESEARCH_METRICS.md`, `CASE_STUDIES.md`, `AGENT_LOOP.md`). Density is unusually high for an OSS project of this size; that's an asset.
- **Stripe-style improvements available:**
  - **Time-to-first-score:** can a new user go from `pip install archy` to a meaningful score output in <60 seconds? Probably yes. Verify on a fresh `cookiecutter-pypackage` and document the exact path in a "60-second tour" at the top of the README. Stripe's KPI translated.
  - **Live runnable example.** Not feasible for a CLI in a docs page, but a hosted asciinema (https://asciinema.org/) embed showing a real session is the OSS equivalent. Already proposed in item 18.
  - **SEO per concept.** Each doc currently lives under `docs/` but isn't optimized for search. Consider:
    - Renaming `SCORING.md` to keep filename but add SEO-friendly H1: "How archy scores Python architectural health (modularity, acyclicity, depth, equality)"
    - Same for other docs. The H1 is what Google indexes.
  - **One docs page per concept-search-term.** Currently several concepts live inside `SCORING.md`. Splitting "Stable Dependencies Principle in Python," "Cyclomatic complexity for module-level decisions," "Cyclic imports in large Python codebases" into separate searchable pages would multiply SEO surface.
- **Marketing-to-experts already on point.** README assumes Python literacy, MCP literacy, CI literacy. Don't soften it.
