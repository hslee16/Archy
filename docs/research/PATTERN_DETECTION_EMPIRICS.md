# Architectural pattern detection from the dependency graph: a documented non-result

Empirical answer to [#288]. The source is [*Architecture Patterns with Python*][cosmic] (Cosmic Python), whose pattern set (Repository, Service Layer, Unit of Work, Aggregates, Domain Events / Message Bus, CQRS, Dependency Injection, all under a Ports & Adapters ideal) is very nearly a set of claims about the import graph. The question the ticket asked was whether archy can turn those claims into a real, low-false-positive structural signal, or whether "pattern detection" is aspiration dressed as a metric.

**Answer: NO-GO, on evidence.** Harness `bench/pattern_detection.py`, full numbers in [`../../bench/pattern_detection_results.md`](../../bench/pattern_detection_results.md).

## TL;DR

| Research question (#288) | Answer |
| --- | --- |
| **Contracts delta** | **Negative, on the axis that matters: transitivity.** On the one control where both were run, a 14-line contract caught coupling laundered through an internal module that every inferred detector missed. Stated narrowly on purpose: contracts were run on one repo, by hand, and were *handed* the domain module names. |
| **Inference vs declaration** | **Inference does not produce a shippable detector.** Four inferred detectors: one is tautological, two produce correct repo-level verdicts from the wrong modules on 2 of 3 controls, and the best-precision one is a naming convention that is vacuous on half the general corpus. |
| **Ground truth (A vs B vs C)** | Repo-level separation of A from B is perfect for both positional detectors and means less than it looks. Domain-identification precision on real controls is 0.00, 0.33, 0.00. |
| **Usage signal** | **Zero.** `ADOPTERS.md` is empty, every issue is maintainer-authored, and nobody has asked archy "do I follow hexagonal architecture?" |
| **Anti-theater / axis gate** | **Fails**, though not where the ticket expected. Not at "score vs binary": the *binary* is the problem, because on two of three controls it is right for reasons unrelated to the architecture it claims to check. |

> This note records the second run of the bench. The first version was refuted on three points by adversarial review (a wrong ground-truth label, self-stack dependencies counted as false positives, and a silently truncated corpus). The verdict survived; several supporting arguments did not. Corrections are tabulated at the end of the results file rather than quietly folded in.

## 1. A substrate fact that shapes everything below

archy's graph is **internal-only**. `graph._external_target` collapses an external dotted path to its top-level package and `assemble_graph` keeps only internal edges. But every pattern in the book is a claim about the boundary between internal code and *external* infrastructure (the ORM, the web framework, the broker).

So none of this is derivable from `archy_graph` as it exists. Every detector below had to drop one layer down, to `parse_project`'s `ParseResult.imports`, to see `sqlalchemy` at all. Any `archy_patterns` surface would therefore not be a thin read over the existing graph; it would need a new external-import derivation carried up through the graph, the index, and the cache. That raises the bar the evidence has to clear.

## 2. Detectors

| detector | how "domain" is decided |
| --- | --- |
| **D_purity** | modules importing nothing non-stdlib |
| **D_naming** | qualname carries `domain`/`model`/`entities`/... |
| **D_position** | internal graph sinks (fan-in > 0, fan-out == 0) |
| **D_position_relaxed** | internal dependencies stay inside the module's own top-2-level package |

`D_position` deserves its rationale stated fairly: "everything depends on it, it depends on nothing" is a genuine reading of the hexagonal ideal, and exactly the kind of thing a dependency graph *should* see. `D_position_relaxed` exists because a strict sink is demanding (cosmic's `domain.model` imports `domain.events`, so it is not a sink), and a negative result should not rest on one operationalization.

The contracts baseline is **not** in the harness. import-linter builds its graph by importing the package, so it needs each project's runtime dependencies installed; it was run by hand on one control repo. Every contracts-vs-inference claim here is scoped accordingly.

## 3. Result 1: the purity detector is tautological

`D_purity` defines the domain as modules importing nothing external, then asks whether those modules import infrastructure. **They cannot, by construction.** Confirmed: 0 flags across all 40 repos, including three controls whose domain classes subclass an ORM base.

Worth naming because the failure is not "weak signal" but "the question was defined out of existence". Same shape as the gate tautology caught in [#298] §5: a detector's definition can quietly guarantee its own answer.

## 4. Result 2: the positional detectors are right for the wrong reason on 2 of 3 controls

At repo level both look like clean wins:

| corpus | D_position flagged | D_position_relaxed flagged |
| --- | --- | --- |
| A (patterns present, n=8) | **0/8** | **0/8** |
| B (patterns absent, n=3) | **3/3** | **3/3** |

Perfect separation. Had the bench stopped at repo-level verdicts, this would have shipped.

It does not survive asking *which modules* were flagged. Domain-identification precision on the controls is **0.00, 0.33, 0.00**:

| repo | D_position flagged | the actual domain |
| --- | --- | --- |
| microblog | `app.cli`, `app.search`, `app.translate` | `app.models` |
| flask-realworld | `conduit.commands`, `conduit.exceptions` | `conduit.*.models` |
| django-realworld | `conduit.apps.core.models`, `.renderers` | `conduit.apps.*.models` |

On microblog and flask-realworld the flags are CLI entrypoints, a translation adapter, and an exception module. Every one is an **adapter**, and adapters are supposed to import infrastructure. The detector produced the right repo-level verdict from modules that have nothing to do with the property being checked.

The django-realworld case is genuinely different, and the first version of this note got it wrong by omitting it. `conduit.apps.core.models` holds `TimestampedModel`, the abstract Django base that all three concrete domain models inherit from. Flagging it is a **real hit** on real ORM-in-the-domain coupling. So the honest claim is not "it never finds the domain": it is that **it finds the domain in one control out of three**, and elsewhere is right by accident.

**The steelman does not rescue it.** `D_position_relaxed` improves recall where it helps (all four labeled modules on django-realworld, recall 1.00 up from 0.25) but precision falls to 0.22, it remains **0.00/0.00 on the other two controls**, and it flags more corpus-C projects (5/29 vs 4/29). Relaxing trades one failure for another.

## 5. Result 3: neither detector can tell "not applicable" from "violating"

Corpus C is 29 general Python projects with no domain/infrastructure split. After subtracting each project's own foundation dependency (counting "fastapi imports starlette" as a layering violation was an error in the first run), `D_position` still emits violations on 4 of 29:

| project | violations | example |
| --- | --- | --- |
| flask | 1/2 | `flask.typing` |
| django | 1/40 | `django.db.backends.postgresql.psycopg_any` |
| home-assistant | 15/843 | `homeassistant.components.denonavr.receiver` |
| dagster | 3/38 | `dagster._core.storage.sqlalchemy_compat` |

The per-module rates are low and each observation is *true*: those modules do import infrastructure. They are meaningless as architecture findings, because none of these projects has a hexagon to violate. A shipped detector would tell a Django maintainer that `django.db.backends.postgresql` violates hexagonal architecture.

This is the discriminant-validity check `AXIS_REVIEW.md` requires, and the productivity-theater failure [#142] warns about, arriving in a form the ticket did not anticipate. #288 assumed a binary "you violate hexagonal in 3 places" would be actionable where "hexagonal score: 0.72" would not. **The binary is the problem**, because a detector with no notion of applicability cannot distinguish a project that violates the pattern from one that never adopted it.

`D_naming` fails here in mirror image: **15 of 29 vacuous**. No module is named `model`/`domain`, so "compliant" is returned with nothing checked.

## 6. Result 4: naming has the precision and the fatal blind spot

`D_naming` has the best domain precision on the controls (1.00, 0.50, 1.00). That makes sense: `models.py` usually is the domain in a Django/Flask app. But:

- **It is not a structural signal.** It reads names. A project calling its domain `core.py` is invisible to it, hence the 52% vacuous rate on corpus C.
- **Its one corpus-A flag is a false positive against the book's own architecture.** It flagged `djangoproject.alloc.models` in `appendix_django`, where that module is deliberately the Django ORM *adapter* and the pure domain lives in `allocation/domain/model.py`. It mistook an adapter named `models` for the domain.
- **It misses transitive coupling**, which decides the contracts question:

```
conduit/user/models.py:5   from conduit.database import Column, Model, SurrogatePK, db
conduit/database.py:3      from sqlalchemy.orm import relationship
```

`flask-realworld` is the most plainly active-record repo in the control set. `D_naming` identified its domain perfectly (1.00/1.00) and still reported **zero violations**, because the coupling is laundered one hop through an internal module.

## 7. Result 5: what the contracts baseline actually showed

14 lines of `.importlinter` (committed at `bench/fixtures/flask_realworld.importlinter`) with `include_external_packages = True`, through the shipped `archy.contracts.run_contracts`:

```
all_kept: False | kept: 0 | broken: 1
  chain: conduit.articles.models -> conduit.database (line 8) -> sqlalchemy (line 3)
  chain: conduit.profile.models  -> conduit.database (line 3) -> sqlalchemy (line 3)
  chain: conduit.user.models     -> conduit.database (line 5) -> sqlalchemy (line 3)
```

All three domain models, full transitive chains, line numbers to fix.

**The comparison is asymmetric and the asymmetry is the point.** The contract is handed the three module names that every inferred detector had to guess, and it was run on one repo out of 40. On that same repo `D_naming` identified the domain just as well (1.00/1.00). So the defensible claim is narrow: **contracts caught the transitive case that all four inferred detectors missed, and supplied remediation chains while doing it.** Transitivity is the axis, not "every axis".

That is still enough to answer the ticket, because transitivity is not a detail. Real coupling hides behind one internal hop routinely, and a direct-import detector is structurally blind to it.

## 8. Why inference fails, stated at the right strength

The four detectors are not four tunings of one idea; they are the available strategies for guessing intent, and each fails characteristically:

| strategy | failure |
| --- | --- |
| define domain by **purity** | violation impossible by construction |
| define domain by **name** | not structural; vacuous where the convention is absent; fooled by adapters named `models` |
| define domain by **graph position** | finds leaf adapters on 2 of 3 controls; cannot tell inapplicable from violating |
| **relax** the positional definition | buys recall, loses precision, flags more inapplicable projects |

The common cause: **"domain" is a statement of intent, and the import graph does not carry intent.** `model.py` and `renderers.py` can occupy identical graph positions; what makes one the domain is a human decision about what the software is *about*. Contracts work because the user supplies that decision.

Stated at the right strength: this is a strong inference from four detectors across 40 repos, not a proof. Someone could build a fifth. What the evidence establishes is that the obvious strategies fail for a legible shared reason, and that the bar is a 14-line contract that already ships.

## 9. The rest of the pattern set

| # | pattern | verdict |
| --- | --- | --- |
| 1 | **Hexagonal / dependency direction** | Detectable **only as a declared contract**, which archy ships. Inference fails (§3-§8). |
| 2 | **Repository** | Same shape and outcome: "only the repo module imports the ORM" is a Forbidden contract with that module whitelisted. Identifying *which* module is the repository has the same intent problem. |
| 3 | **Service Layer** | Requires distinguishing "thin ring of entrypoints" from "any module with moderate betweenness". This is §4's failure with an extra step. |
| 4 | **Message Bus / Handler** | Detectable only as the *absence* of direct edges plus a central dispatcher. Absence of an edge is unfalsifiable: it is also what two unrelated modules look like. No detector was built; the design cannot produce a negative. |
| 5 | **Unit of Work / Aggregate / CQRS / DI** | Claims about runtime semantics, object identity, and transaction boundaries. Not visible to static imports. Recorded so it is not re-proposed. |

## 10. Honest limitations

- **Corpus B is n=3.** Three framework-coupled apps is a small control set, and one of the three (django-realworld) behaves differently from the other two. A wider control set would sharpen the 0.00/0.33/0.00 picture.
- **Corpus A is n=8 of 11 branches.** The book's first chapters are flat root-level `.py` files with no package, so archy finds no package root. Those chapters contain no infra to violate against.
- **All corpus A and B repos are small** (13 to 44 modules); cosmic's are teaching repos by design.
- **Ground truth is single-labeler.** The first run's `django-realworld` label was wrong and materially changed a headline number, which is the best available evidence that this limitation is real.
- **The contracts baseline is n=1**, hand-run, and oracle-fed (§7).
- **`INFRA_PACKAGES` and `SELF_STACK` are curated lists.** The first run's corpus-C "false positives" turned out to be entirely a curation artifact, so this is not a marginal caveat: curation choices moved the headline once already.
- **Only two positional operationalizations were tested** (§8).

## 11. Disposition

| Item | Call |
| --- | --- |
| `archy_patterns` surface, in any form | **Wontfix.** §8. |
| Hexagonal / dependency-direction detector | **Wontfix as inference.** Already shipped as a declared contract. |
| Repository / Service Layer detectors | **Wontfix**, same failure mode (§9). |
| Message Bus / UoW / Aggregate / CQRS / DI | **Not graph-detectable.** Recorded in §9. |
| Docs | Worth one line in the contracts docs noting that `include_external_packages = True` is how you express "the domain must not import the ORM", since that is the shipped answer to the question this ticket asked. |

**Reopen path.** A positive result needs something that supplies intent without the user writing it down: inferring the domain from a source archy does not read today (docstrings, directory conventions declared in `pyproject.toml`, an LLM labeling pass). Each is a different project from "detect patterns from the dependency graph", and each would have to beat the 14-line contract in §7 on transitivity and remediation, and beat `D_naming` on domain precision, before it was worth shipping.

## Related

- [#124] constraint-conformance / surprise-rate signal
- [#139] rule rot / constraint staleness
- [#271] intended-vs-actual conformance score, the declared-intent framing that does work
- [#298] the framework-integration non-result, whose §5 tautology has the same shape as §3 here
- [`AXIS_REVIEW.md`](AXIS_REVIEW.md) for the OECD discriminant-validity check applied in §5
- [`../../bench/pattern_detection_results.md`](../../bench/pattern_detection_results.md) for the full run and the correction log

[cosmic]: https://www.cosmicpython.com/book/preface.html
[#124]: https://github.com/hslee16/Archy/issues/124
[#139]: https://github.com/hslee16/Archy/issues/139
[#142]: https://github.com/hslee16/Archy/issues/142
[#271]: https://github.com/hslee16/Archy/issues/271
[#288]: https://github.com/hslee16/Archy/issues/288
[#298]: https://github.com/hslee16/Archy/issues/298
