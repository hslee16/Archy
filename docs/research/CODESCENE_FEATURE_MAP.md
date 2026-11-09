# CodeScene's features, and where archy stands on each

Fact-gathering date: 2026-11-09. Sources are CodeScene's own documentation pages,
read in full for this table (links per row), plus `cs docs` and the `cs` CLI. The
companion document [`CODESCENE_HEALTH_MODEL.md`](CODESCENE_HEALTH_MODEL.md) covers the
Code Health scoring in detail; this one maps the whole product. "archy today" was
checked against the code and `archy --help` at the commit this was written.

The request behind it: archy should be able to give a user the feedback CodeScene does.
Each gap is either a ticket or an explicit decision not to build it, with the reason.

## The map

| CodeScene feature | What their docs say it does | archy today | Decision |
|---|---|---|---|
| **Code Health** ([guide](https://codescene.io/docs/guides/technical/code-health.html)) | Per-file score 1 to 10 from function, implementation and module smells; project figure is a LoC-weighted average | `archy.complexity` (cyclomatic per function) feeds the score's `complexity` axis; no smells, no per-file score | #871 (`archy health`); reproducibility measured in #870; archy's score axes unchanged (decision on #886) |
| **Hotspots / technical debt** ([guide](https://codescene.io/docs/guides/technical/hotspots.html)) | Prioritises debt by how the team changes the code: code health combined with change frequency | `archy hotspots` (cyclomatic x churn), `archy what-to-refactor-next` (fused with edit risk) | Exists; label defect #880; a health term in #871 |
| **X-Ray** ([guide](https://codescene.io/docs/guides/technical/xray.html)) | Hotspots at **method** level: which functions inside a large file are worth refactoring first; run on demand and cached | Module-level only; per-function complexity exists but no per-function churn | #902 |
| **Complexity trends** ([guide](https://codescene.io/docs/guides/technical/complexity-trends.html)) | How a hotspot evolves: getting more complicated, or minor changes to a stable structure | `archy trend` is the project score over history; nothing per file | #903 |
| **Architectural analyses** ([guide](https://codescene.io/docs/guides/architectural/architectural-analyses.html)) | Hotspots, trends and change coupling at the level of logical components, not files | `archy.yaml` already declares layers; hotspots and coupling are per module | #904 |
| **Change coupling** ([guide](https://codescene.io/docs/guides/technical/change-coupling.html)) | Modules that change together over time | `archy coupling` | Exists |
| **Goals** ([guide](https://codescene.io/docs/guides/technical/augmented-analysis.html)) | Attach refactoring and supervise goals to hotspots and track them | Nothing per file; `archy diff --strict` is a ratchet on cycles and layer violations | Folded into #872 and #873, not a separate ticket |
| **Code churn** ([guide](https://codescene.io/docs/guides/technical/code-churn.html)) | Rate at which code evolves | `git_churn` inside `hotspots` and `what-to-refactor-next`; `git_cochange` inside `coupling` | Exists (internal) |
| **PR integration and quality gates** ([guide](https://codescene.io/docs/guides/pr-integration/integrate-into-ci-cd.html)) | Delta review of a pull request; profiles Bare Minimum, Clean Code Collective, Pay Down Tech Debt; per-path rule sets in `.codescene/custom-quality-gates.json` | `score-delta.yml` and `score-comment.yml` post a score delta (#604); `diff --strict` ratchet | #872, #873, #884; **new: `archy diff` against a git ref, #905** |
| **CLI delta** ([CLI](https://codescene.io/docs/cli/index.html)) | `cs delta <base>`: compare the working tree or a branch with a base, no stored baseline | `archy diff` needs a stored snapshot file | #905 |
| **MCP server** ([guide](https://codescene.io/docs/developer-tools/mcp/codescene-mcp-server.html)) | Tools for code health, `list_technical_debt_hotspots`, `list_technical_debt_goals`, `code_health_refactoring_business_case`, aimed at checking AI-written code | `archy mcp`: 14 tools, including `archy_what_to_refactor_next` (its behavioral lens is hotspots) | Exists; see decisions below |
| **ACE auto-refactor** ([guide](https://codescene.io/docs/auto-refactor/index.html)) | LLM-driven refactoring of Large Method, Deep Nested Logic, Bumpy Road, Complex Conditional, Complex Method | None | Not proposed |
| **Knowledge distribution, bus factor, team alignment** ([guide](https://codescene.io/docs/guides/social/knowledge-distribution.html)) | Key personnel risk, knowledge islands, coordination bottlenecks | None | Not proposed |
| **Delivery and the 4 factors** ([guide](https://codescene.io/docs/4f-dashboard/4-factors-dashboard.html)) | Lead time, waste, speed and risk dashboards | None | Not proposed |

## Not proposed, and why

- **Business case and ROI** (`code_health_refactoring_business_case`). It rests on the
  vendor's statistical model and industry benchmarks, which archy does not have and
  cannot check. archy's own record is that every pre-registered claim it measured came
  back null (`docs/WHAT_DIDNT_WORK.md`), and a refactoring ROI figure is the shape of
  claim that record warns against.
- **ACE auto-refactor.** archy is a sensor for agents that edit code themselves; an
  LLM refactorer is a different product, and nothing here has been measured to help.
- **Knowledge distribution, bus factor, team alignment, delivery metrics.** They need
  author and ticket data about people, not about code structure, which is outside what
  archy reads and raises privacy questions archy has never had to answer. If a usage
  signal ever asks for it, it is a separate research ticket.

## What follows from the gaps

Five tickets, each with its own vetting against the anti-theater test and its own
definition of done: method-level hotspots (#902), per-file complexity trend
(#903), hotspots and coupling per layer (#904), `archy diff` against a git
ref (#905), and repeated decorator declarations in `archy conventions`
(#906, a research ticket first).

## Limits of this map

- It is built from CodeScene's public documentation and CLI. Features behind their
  hosted dashboard that the docs describe only in screenshots were not inspected.
- "archy today" is a statement about this repository on 2026-11-09, not a comparison
  of quality. Having a command with a similar name says nothing about whether it is as
  useful, and none of this shows that any CodeScene feature predicts defects or agent
  success (see `CODESCENE_HEALTH_MODEL.md`, "What this document does not claim").
