# Adam Jacob (Chef co-founder): Sustainable Free and Open Source Communities + Community Compact

**Sources:**
- https://medium.com/sustainable-free-and-open-source-communities/we-need-sustainable-free-and-open-source-communities-edf92723d619
- https://medium.com/@adamhjk/introducing-the-community-compact-431c61ab978f
- https://about.scarf.sh/post/navigating-the-complexities-of-open-source-commercialization-insights-from-adam-jacob
**Type:** Practitioner essays from a successful OSS founder (Chef → System Initiative)
**Date accessed:** 2026-05-16

(Aside: I originally cited "The New Kingmakers" to Jacob; that's actually Stephen O'Grady at RedMonk. Different book. Jacob's contribution is the SFOSC project + Community Compact.)

## Key insights

- **Sustainable-community framing > business-model framing.** The right question is "how do we make this community sustainable" not "how do we monetize this OSS." Communities outlast business models.
- **Be honest about commercial intent.** If you intend to commercialize, say so up front. Bait-and-switch ("free forever" → license change) is the dominant trust failure of the 2010s-2020s OSS scene (MongoDB, Elastic, Redis, HashiCorp, etc.).
- **The Community Compact:** explicit social contract between maintainer and community. Covers what the maintainer commits to (responsiveness, transparency, no rug-pulls) and what the community is expected to do (contribute back, respect scope).
- **Right-to-fork is the trust anchor.** A healthy community is one where forking is *technically possible and culturally legitimate* even if no one does it. License + governance should make this real.
- **Marketing implication:** building a sustainable community *is* the marketing. The community itself becomes the evangelist, the support layer, the recruiting pipeline.

## Applicability to archy

- **State commercial intent now, in the README.** Per item 20, "free / MIT / no commercial version planned" is the trust statement. If the long-term plan is "maybe hosted version someday," say *that* explicitly, not nothing. Honesty pre-empts the freemium-trap suspicion.
- **Right-to-fork is legitimate.** MIT license makes this technically true. Cultural legitimacy: don't add CLAs, don't add "contributor license agreements," don't add anything that signals "we might re-license later." The current state is clean; keep it clean.
- **Mini "compact" in CONTRIBUTING.md.** Even one paragraph: "What I commit to as maintainer: respond to issues within X days, no surprise re-licensing, public roadmap. What I ask of contributors: open an issue before a large PR, follow the no-em-dash style rule, write tests." This is a small but high-trust signal.
- **Sustainability over scale.** Resist any framing that optimizes for "how do we get to 10k stars." The right framing is "how do we make this useful to the 50-200 Python teams that actually need it." That's the club-shaped path per Eghbal, the project-community-fit per Levine, and the sustainable-community per Jacob; three sources, same answer.
