# archy promotion plan

**Status:** COMPLETE (15/15 sources covered)
**Last updated:** 2026-05-16
**Source notes:** see `01-fogel-publicity.md` through `15-adam-jacob-sustainable-oss.md` in this directory.

---

## Positioning baseline (consensus across all 15 sources)

archy is a **club-shaped project** (Eghbal): Python-infra + AI-coding niche, users who become contributors. Not stadium-shaped. The failed Show HN (1 upvote, May 2026) is empirical confirmation; HN is stadium-shaped attention. Promotion strategy: depth in niche, not viral reach.

archy is **pre-PCF** (project-community fit, per Levine/a16z): the right metrics are stars, PyPI downloads, third-party mentions, issues from non-maintainers. Not revenue.

archy is in the **AI-coding-agent ecosystem at exactly the right moment** (Latent Space / TNS / dev.to weeklies 2026): Claude Code overtook Copilot+Cursor; MCP is mainstream; engineers run 2-4 tools at once; cross-tool MCP positioning is the leverage point.

---

## Action plan, prioritized

### Tier 0: pre-flight (do first, in this order; ~1 week total)

**Progress as of 2026-05-16:** items 1, 2, 3, 4, 5, 6, 8 shipped in PR #83. Items 7 (OpenSSF badge) and 9, 10 (Glama/Smithery refresh, fresh-install smoke test) remain because they require external action (badge submission, edits on external sites, multi-OS testing).


These are credibility/readiness items that make every later promotion attempt land harder. Cheap, durable, no audience risk.

1. **README hero upgrade**; add a 2-line "Install + first command" block above the prose; add a 10s asciinema/vhs GIF of `archy hotspots` against a real repo above the fold. [src: Evil Martians teardown]
2. **Origin story**; add 1-2 sentences to README "Why" naming the personal itch (you kept watching Claude Code rot import graphs; you wanted a sensor). [src: ESR]
3. **Maintainer legibility**; one-line "Built by [name + link]" in README. [src: Levine]
4. **Trust statements**; explicit "free / MIT / no commercial version planned" line; if a hosted version is even hypothetical, say so honestly. [src: Adam Jacob, Evil Martians]
5. **"Used by" placeholder**; add `ADOPTERS.md` (empty starter + PR-welcome note) and an "In the wild" section near top of README. Start populating as users surface. [src: CNCF, Evil Martians]
6. **CNCF-style credibility kit PR**; `CODE_OF_CONDUCT.md` (Contributor Covenant), `MAINTAINERS.md` (one line, BDFL), `GOVERNANCE.md` (simple BDFL note), `docs/ROADMAP.md` (promoted from README, with explicit "deferred" / "rejected" sections). Half-day total. [src: CNCF, GitLab]
7. **OpenSSF Best Practices badge**; ~2hr self-assessment at bestpractices.dev. Permanent badge. [src: CNCF]
8. **Mini Community Compact** in `CONTRIBUTING.md`; one paragraph: what I commit to as maintainer (responsiveness, no surprise re-licensing, public roadmap), what I ask of contributors. [src: Adam Jacob]
9. **Cross-check Glama + Smithery descriptions are current to v0.19.0.** Stale directory copy is a free loss. [src: MCP distribution]
10. **Self-serve onboarding hardening**; verify `pip install archy && archy score .` works clean on macOS/Linux/Windows on a fresh `cookiecutter-pypackage`; document the 60-second tour in README. [src: Eghbal scope-discipline + Stripe time-to-first-value]

### Tier 1: channels archy is positioned for *right now* (do after Tier 0; high ROI per hour)

The AI-coding-agent ecosystem is the most under-exploited channel set archy currently has access to. None of these are "spent" (per user). All are niche-depth (Eghbal-correct for a club project).

11. **MCP-directory clerical sprint (remaining 4):** mcp.so, PulseMCP, official `modelcontextprotocol/servers` registry (upstream PR), `awesome-mcp-servers` (upstream PR). Build a reusable submission kit once (tagline, 200-char desc, install snippet, screenshot, 60s demo) → each subsequent submission is ~30 min. Smithery uses "skills" terminology in 2026; adapt copy there. [src: MCP distribution]
12. **`awesome-claude-code-toolkit` and similar curated lists**; PR each one with archy as an MCP server entry. ~30 min each. [src: AI coding agent channels]
13. **One adapted post per AI-coding community** (same content, surface-appropriate framing):
    - r/ClaudeAI: "I built an MCP server that catches when Claude Code rots your architecture"
    - dev.to with `#claudecode #mcp #python`: longer how-to
    - Anthropic Discord #showcase: 60s demo video
    - Cursor Discord, Cline Discord: cross-tool angle ("works in any MCP client")
14. **Latent Space submission**; pitch as "first MCP server giving coding agents architectural feedback before they commit." Audience match is unusually high. [src: AI coding agent channels]
15. **`docs/MCP_DIRECTORIES.md`**; checklist file tracking submission URLs + last-update dates per release. Make directory updates a release-checklist item, not ad-hoc. [src: MCP distribution]
16. **MCP_DIRECTORIES.md should pin the cross-tool framing**; "MCP-native, works with any client, not just Claude"; explicit so it carries through every submission. Engineers run 2-4 tools; tool-specific positioning loses the larger audience. [src: AI coding agent channels]

### Tier 2: the two written assets to make and push (highest single-piece ROI)

These are the two pieces of content with the highest expected ROI per all 15 sources. Both ladder up to the same outcome (project-community fit) via different mechanisms.

17. **"The N best open-source Python architecture tools"** roundup. Honestly covers pydeps, snakefood, import-linter, pylint's import graph, archcheck, archy. Be a fair broker; self-include credibly. PostHog's #1-performing piece was the analogue. Distribution: personal blog → r/Python → Python Weekly → Lobsters → Architecture Weekly → HN (link post, NOT Show HN). [src: PostHog]
18. **"Why your codebase needs a sensor, not a linter"** opinion piece. Cite the Navigation Paradox + LocAgent research already in `docs/RESEARCH_METRICS.md`. Controversial-stance posts dominate per PostHog; cross-niche legs (MCP audience + Python-infra audience + agentic-coding audience). [src: PostHog, ESR; memorable framing is itself promotion]

Distribution playbook for both pieces (same channels, sequence matters):
1. Publish on personal blog or `docs/posts/` (canonical URL).
2. Day 1: Bluesky, X, LinkedIn, Mastodon; short teaser + link.
3. Day 1-2: dev.to cross-post with canonical link.
4. Day 2: r/Python (read sidebar rules; non-promotional framing).
5. Day 3-4: Python Bytes "tell us what you're working on" form; Python Weekly submission.
6. Day 5: Lobsters submission (account-age gated; ask for invite if not already on).
7. Day 7: HN link post; submit at Tue-Thu 8-10am PT. ONE attempt only.

### Tier 3: sustained build-in-public substrate (ongoing, in parallel with Tier 1/2)

19. **Weekly "Build Log"**; 100 words + screenshot on Bluesky + dev.to. Run 8-12 weeks. Builds the small audience that bigger pushes activate. [src: Supabase / Evil Martians]
20. **DQL log**; `research/dql.md`: every external interaction (issue, mention, DM) → routed-to-what artifact (README change, doc, roadmap item, testimonial). ROI = artifacts produced, not stars. [src: Thengvall]
21. **PCF dashboard**; weekly persist: PyPI downloads, GH stars, non-maintainer issues, social mentions, Glama score → `.archy/pcf.jsonl`. Same pattern as `archy score --record`. [src: Levine]
22. **Solicit testimonials.** When a user says something positive ("archy caught the layer violation our reviewer missed"), ask: "would you mind if I quoted this in the README?" One in-context quote from a recognizable engineer > 100 stars. [src: Thengvall]
23. **Public release retros**; even one paragraph per release: "what we tried and walked back this cycle." Cheap, signals seriousness. [src: GitLab]
24. **Per-concept docs SEO splits.** Split `SCORING.md` etc. into one search-term-optimized page per concept ("Stable Dependencies Principle in Python," "Cyclic imports in large Python codebases"). H1 = the search term. [src: Stripe]

### Tier 4: deferred (re-evaluate in ~6 months when there's a small core audience)

25. **Solo-maintainer Launch Week**; 5 weekdays, one ship + one post per day. Prerequisites: items 19 + 22 have produced a small but real audience; 5 features genuinely ready to ship. Don't ship green features for the calendar; that kills credibility. Candidate ships: `archy dsm`, call-density score axis, new MCP tool, hotspots v2, benchmark refresh. [src: Supabase]
26. **Decide whether to commit `research/oss-marketing/` to the repo** at the same time. Committing is itself a transparency move that signals systematic thinking and earns GitLab-handbook-style credibility. [src: GitLab]

### Permanently OFF the table for archy

- **Second Show HN.** HN policy disallows re-launch; first attempt got 1 upvote. Don't suggest again. [src: HN guidelines + user state]
- **"Support and services" business model** if/when monetization is ever considered. Has not worked outside Red Hat in 20 years. The viable path is open-core + hosted. [src: Levine]
- **CLAs or contributor license agreements**; kills the right-to-fork trust anchor. [src: Adam Jacob]

---

## Tracking

- Tier 0 is a checklist of 10 items. Target: 1 week.
- Tier 1 is ~6 items + 4 community posts. Target: 2 weeks after Tier 0.
- Tier 2 is 2 pieces of writing. Target: 1 month after Tier 1.
- Tier 3 is ongoing.
- Tier 4 is a re-evaluation date, ~Nov 2026.

Re-read SYNTHESIS.md at each tier-completion and update with what actually happened. The plan is provisional; the source notes are durable.

---

## Sources covered (15)

1. Karl Fogel, *Producing Open Source Software*, Publicity chapter; `01-fogel-publicity.md`
2. Nadia Eghbal, *Working in Public* (2020); `02-eghbal-working-in-public.md`
3. PostHog content-marketing playbook; `03-posthog-content-playbook.md`
4. Show HN platform guidelines (retrospective; already spent); `04-show-hn-launch.md`
5. Peter Levine (a16z), *From Community to Commercialization*; `05-a16z-levine-oss-commercialization.md`
6. Mary Thengvall, *The Business Value of Developer Relations*; `06-thengvall-devrel.md`
7. MCP-ecosystem distribution (Glama / Smithery / others); `07-mcp-directory-distribution.md`
8. GitLab handbook on transparent marketing; `08-gitlab-handbook.md`
9. Supabase Launch Week format; `09-supabase-launch-week.md`
10. Evil Martians 100 dev-tool landing pages teardown (2025); `10-evilmartians-devtool-landing-pages.md`
11. Eric Raymond, *The Cathedral and the Bazaar*; `11-raymond-cathedral-bazaar.md`
12. CNCF project graduation criteria; `12-cncf-graduation.md`
13. Stripe; documentation as marketing; `13-stripe-docs-as-marketing.md`
14. AI coding agent ecosystem channels (2026); `14-ai-coding-agent-channels.md`
15. Adam Jacob; Sustainable FOSS + Community Compact; `15-adam-jacob-sustainable-oss.md`

## Loop end

Stop condition met: 15+ distinct sources, concrete prioritized plan. The pending `ScheduleWakeup` at 11:54 PT will fire once; that turn will not re-arm.
