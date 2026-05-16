# Evil Martians: 100 dev-tool landing pages teardown (2025)

**Source:** https://evilmartians.com/chronicles/we-studied-100-devtool-landing-pages-here-is-what-actually-works-in-2025
**Type:** Industry research / teardown
**Date accessed:** 2026-05-16

## Key insights

- **Modal hero shape:** centered, big bold headline, single supporting visual or short demo GIF below. "Stable, trustworthy"; devs trust convention here.
- **Headline grammar:** short noun-phrase + sharp verb, no marketing-speak. Examples that work: "Background jobs for modern web applications" (Trigger.dev), "The intelligent meeting assistant" (Granola).
- **CTAs:** "Start building", "Install", "Read the docs", "Star on GitHub" beat generic "Get Started."
- **Clients section directly under hero** = fastest credibility signal. Logo wall of recognizable users.
- **Minimal animation.** Devs distrust marketing flash. Solid typography, breathing room, clear structure.
- **Pricing transparency** scored as a trust signal; even free/OSS projects benefit from a "free forever / paid hosted" frame if one exists.
- **Code snippets visible above the fold** for libraries and CLIs; readers want to see what using the thing looks like before they scroll.

## Applicability to archy

- **archy has no landing page; only the GitHub README serves that role.** That's fine for now; the README *is* the landing page for OSS libraries. The teardown principles still apply.
- **README hero check (current state):** ✅ short headline ("Architectural sensor for Python codebases; keeps structure honest under AI-assisted development") ✅ short demo (the mode table) ✅ code snippet (`pip install archy`). Already good.
- **What's missing per the teardown:**
  - **Clients/users section.** A "Used by" or "In the wild" section near the top with even 2-3 logos / project names would help massively. Solicit explicitly: if any user mentions running archy in CI on a real project, ask for permission to list them.
  - **Demo GIF or video above the fold.** Currently the README is text-only until line ~60. A 10-second terminal-recording GIF of `archy hotspots` against a real repo would carry significant weight. asciinema or vhs.
  - **Single, hero CTA.** Currently the README jumps straight into the mode table. Consider a 2-line "Install: `pip install archy` → Run: `archy score .`" hero block before any prose.
- **If/when a dedicated landing page exists** (e.g., archy.dev): copy the Linear / Trigger.dev / Resend pattern; centered hero, code snippet, logo wall, opinion-block, docs link. No carousels, no testimonial videos.
- **Pricing transparency**: not relevant yet (pure OSS), but the README should explicitly say "free / MIT / no commercial version" to pre-empt the "is this a freemium trap?" question that experienced devs ask now.
