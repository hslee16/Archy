# Research

Curated, published research behind archy's design decisions: the literature survey and the empirical studies that gate which metrics ship, get deferred, or get rejected. These are the version-controlled, citable counterparts to raw working notes; they are referenced from the [README](../../README.md), [`ROADMAP.md`](../ROADMAP.md), and [`FUTURE.md`](../FUTURE.md).

| Doc | What it is |
| --- | --- |
| [`RESEARCH_METRICS.md`](RESEARCH_METRICS.md) | The literature survey: ~15 architecture-quality metrics evaluated for Python-specific applicability, plus the AI-agent-feedback section (§14) tying archy to the 2025-2026 coding-agent research (Navigation Paradox, LocAgent, Constraint Decay). The catalogue that informs the roadmap. |
| [`AXIS_REVIEW.md`](AXIS_REVIEW.md) | The OECD composite-indicator framework applied to every score-axis-promotion decision (why 5 axes, why `calls_per_edge` is not a 6th). |
| [`CALL_WEIGHTED_Q_EMPIRICS.md`](CALL_WEIGHTED_Q_EMPIRICS.md) | Empirical study behind v0.21's call-weighted Newman Q diagnostic (why it ships as a parallel diagnostic, not an axis replacement). |
| [`DSM_EMPIRICS.md`](DSM_EMPIRICS.md) | Why `archy dsm` ships as visualization-only and no DSM-derived scalar becomes a score axis or diagnostic. |
| [`SCORE_SHAPE_REDESIGN_EMPIRICS.md`](SCORE_SHAPE_REDESIGN_EMPIRICS.md) | The 28-project study of acyclicity/depth reformulations and aggregator alternatives that concluded against any axis change. |
| [`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md) | The study that rejected type-hint coverage in both axis and diagnostic form. |
| [`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md) | Synthesis of Tracy Bannon's AI Autonomy Continuum talk: where archy sits on the continuum, the productivity-theater / score-gaming risk, and autonomy-tiered gating (deterministic checks may block, inferential checks stay advisory). Positioning and vocabulary, not new metrics. |

For *why we built what we built* (retrospective design rationale and competitive positioning), see [`../LEARNINGS.md`](../LEARNINGS.md). For the forward roadmap, see [`../ROADMAP.md`](../ROADMAP.md) and [`../FUTURE.md`](../FUTURE.md).
