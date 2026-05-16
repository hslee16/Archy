# Show HN launch mechanics

**Source:** https://news.ycombinator.com/showhn.html
**Type:** Platform guidelines + community norms
**Date accessed:** 2026-05-16

## Key insights

- **Title must start with "Show HN: ".** No version-bump posts ("X 2.0") unless it's a genuine rewrite.
- **No signup, no email gate.** A reader must be able to try the thing in <60 seconds.
- **Explain how and why you built it** in the first comment (yours, as author).
- **Be there for 4–8 hours after posting.** Reply to every comment, including hostile ones. Suggest alternatives instead of dismissing.
- Polish optional, but *seriousness* required: no quickly-generated one-offs.
- No upvote rings; HN detects them and flags submissions.

## Status note (2026-05-16)

**archy already launched on Show HN in early May 2026.** That slot is spent; HN policy disallows re-launch. Below recommendations are retained for *retrospective lessons* and for *future, distinct projects*; they are NOT a playbook to re-execute on archy.

Immediate retro actions for archy:
- Pull the Show HN URL + comment thread into `research/oss-marketing/04b-show-hn-retro.md` (next iteration).
- Score each comment: bug report, feature request, positioning critique, comparison-with-X question, or pure noise. Each non-noise comment maps to a README/doc/roadmap delta.
- Reply to any unanswered comments now; HN posts get long-tail traffic for weeks.
- Future external link-posts to HN (a blog post submitted as a normal URL) are still allowed and not subject to the one-shot rule.

## Applicability to archy (pre-launch hypothetical, retained for reference)

- archy passes the "no signup" bar trivially: `pip install archy && archy score .`. Good.
- **Title to test:** "Show HN: archy; architectural sensor for Python codebases under AI-assisted development." The "AI-assisted" angle is HN catnip in 2025-2026; the "sensor" framing distinguishes from linter.
- **First comment template** should cover: (1) why I built it (AI agents drift architecturally), (2) what it does in one sentence, (3) what it does *not* do (multi-language, code generation, replacing linters; pre-empt the most common "is this just X" question), (4) honest comparison to import-linter and pydeps, (5) MCP-server angle as the genuinely-new part.
- **Pre-empt the predictable critiques:**
  - "Why not just use import-linter?"; answer: archy wraps it (`archy contracts`) and adds the score/trend/MCP layer.
  - "Why a new score?"; answer: link to `docs/SCORING.md` and the 27-project benchmark.
  - "AI slop?"; answer: point at the research-backed roadmap (RESEARCH_METRICS.md §14c).
- **Do not launch on Show HN until** the install path is verified clean on macOS/Linux/Windows and `archy score .` on a fresh `cookiecutter-pypackage` project returns a sensible number. One broken first-try install kills the thread.
- **Best day/time:** Tuesday-Thursday, 8-10am Pacific. Avoid Mondays (Show HN queue is backed up) and weekends (low traffic).
- **Plan one (and only one) Show HN.** Re-launches are frowned upon. Save it for a milestone: 1.0, or a genuinely-novel MCP capability.
