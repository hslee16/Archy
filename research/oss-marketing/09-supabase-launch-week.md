# Supabase: launch week format + content production model

**Sources:**
- https://supabase.com/blog/supabase-how-we-launch
- https://evilmartians.com/chronicles/how-to-do-launch-weeks-for-developer-tools-startups-and-small-teams
- https://openapps.pro/blog/from-1m-to-4m-developers-supabase-coss-growth-playbook
- https://github.com/supabase-community/launchweek.dev
**Type:** Vendor blog + cross-industry analysis
**Date accessed:** 2026-05-16

## Key insights

- **Launch Week format:** ship one major feature or announcement per day for ~5 days, on a fixed calendar quarter. *Fixed timeline, flexible scope.* The deadline does most of the work: shipping discipline + concentrated audience attention.
- This is now an industry standard for devtools (Vercel, Cal.com, PlanetScale, Trigger.dev, Resend all run their own variants).
- **Engineer-authored content.** The person who built the feature writes the launch post. Yields deep-technical posts that read as credible to other engineers, not as marketing copy.
- **Build-in-public substrate.** Public roadmap, public Discord, public Twitter/X account that posts shipping progress weekly. Launch Week sits on top of an always-on transparency baseline.
- **Community channels as multipliers.** Supabase Discord (200k+) + Twitter + dev.to + YouTube. Launch Week posts hit all of them on a synchronized schedule.

## Applicability to archy

- **A solo-maintainer Launch Week is feasible at smaller scale.** Concrete proposal:
  - 5 weekdays, one ship + one post per day.
  - Candidates currently in flight per archy roadmap: `archy dsm` (Design Structure Matrix), promoting call-density to a score axis, a new MCP tool, a hotspots improvement, a benchmark refresh.
  - Each day's post: 300-500 words, written by the maintainer (already is), framed as "today archy shipped X; here's why it matters for AI-assisted coding."
  - Pre-announce the week 2 weeks ahead on Bluesky/X/r/Python.
  - Stagger publishes across PyPI release + GitHub release + blog (personal or repo `docs/launches/`) + cross-posts to dev.to + 4 MCP directories.
- **Pre-requisite work:**
  - 5 features ready to ship within 2 weeks (don't ship green features for the sake of it; kills credibility).
  - A blog surface that's not just GitHub releases. `docs/launches/2026-Q3/dayN.md` is the cheap option. Personal blog is better if one exists.
  - A short list of subscribed humans (Bluesky followers + email list + GH "watch" list) so day 1 doesn't ship into a vacuum.
- **Tradeoff: don't do this prematurely.** Per Eghbal, archy is club-shaped; a Launch Week before there's a small core audience is a stunt with no audience. Defer until there's at least one or two unsolicited blog posts / mentions in the wild. Right now: not yet.
- **Cheaper precursor: "Build Log."** Weekly 100-word note + screenshot of what shipped that week, posted Bluesky + dev.to. Run for 8-12 weeks before considering Launch Week. Builds the audience that Launch Week then activates.
