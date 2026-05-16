# Eric Raymond: The Cathedral and the Bazaar (1997, foundational)

**Source:** https://en.wikipedia.org/wiki/The_Cathedral_and_the_Bazaar and https://www.willpatrick.co.uk/notes/the-cathedral-and-the-bazaar-eric-raymond
**Type:** Essay (foundational OSS text; persuaded Netscape to open Mozilla)
**Date accessed:** 2026-05-16

## Key insights (the lessons that still matter for marketing)

- **"Every good work of software starts by scratching a developer's personal itch."** The origin story matters. Users believe in tools built by people who needed them.
- **"Release early, release often."** Public iteration is itself a trust signal. Hidden development reads as either dead or unconfident.
- **"Given enough eyeballs, all bugs are shallow"** (Linus's law). Public visibility is the QA mechanism *and* a recruiting funnel.
- **"Treat users as co-developers."** Encourage them to send patches, file diagnoses, suggest features publicly. They feel ownership; you get free QA.
- **"Smart data structures and dumb code work better than the other way around."** Tangential to marketing but a positioning move: tools whose value is in the model, not the cleverness, are easier to explain.
- The cathedral/bazaar dichotomy itself is the marketing artifact most cited from this essay; having a memorable framing for *how you work* is itself promotion.

## Applicability to archy

- **Origin story is currently buried.** The README's "Why" section explains the *technical* motivation (AI agents drift) but not the *personal* one. Add 1-2 sentences: "I built this because I kept watching Claude Code generate code that passed review but rotted my import graph. I wanted a sensor that would catch it." This is the "scratch your own itch" hook Raymond identifies as foundational. Real, specific, human.
- **Release cadence is good.** v0.15 → v0.19 in recent commits = "release early, release often" applied. The risk per Fogel is over-firing announcement channels, but the *cadence* itself is on-strategy.
- **"Users as co-developers" gap:** README does not currently invite users in. Add to README: "Found a bug or missing metric? File an issue with a link to your repo and I'll usually look the same week." Lowering the perceived bar to interaction is critical for a club-shaped project per Eghbal.
- **Memorable framing for archy:** "sensor, not a linter" is already in the README and is good. Strengthen it: an essay-length post titled "Why your codebase needs a sensor, not a linter" would do for archy what cathedral/bazaar did for OSS as a concept; give people a phrase to repeat.
