# Architectural pattern detection from the dependency graph: a documented non-result

Empirical answer to [#288]. The source is [*Architecture Patterns with Python*][cosmic] (Cosmic Python), whose pattern set (Repository, Service Layer, Unit of Work, Aggregates, Domain Events / Message Bus, CQRS, Dependency Injection, all under a Ports & Adapters ideal) is very nearly a set of claims about the import graph. The question the ticket asked was whether archy can turn those claims into a real, low-false-positive structural signal, or whether "pattern detection" is aspiration dressed as a metric.

**Answer: NO-GO, on evidence.** Harness `bench/pattern_detection.py`, full numbers in [`../../bench/pattern_detection_results.md`](../../bench/pattern_detection_results.md).

## TL;DR

| Research question (#288) | Answer |
| --- | --- |
| **Contracts delta** | **Negative.** Inference loses to declaration on every axis measured. A 12-line `.importlinter` contract catches the hardest control case with transitive chains and line numbers; the best inferred detector misses it or gets it right for the wrong reason. |
| **Inference vs declaration** | **Inference does not work, and the reason is structural, not a tuning problem.** Every purely graph-derived definition of "domain" either makes the violation impossible by construction, collapses into a naming convention, or picks out leaf adapters that are not the domain. |
| **Ground truth (A vs B vs C)** | The best detector separates patterns-present from patterns-absent **perfectly at repo level** and has **0.00 domain-identification precision on every control**, plus a 20% false-positive rate on projects where the question does not apply. |
| **Usage signal** | **Zero.** `ADOPTERS.md` is empty, every issue is maintainer-authored, and nobody has asked archy "do I follow hexagonal architecture?" |
| **Anti-theater / axis gate** | **Fails.** Not at the "score vs binary" level the ticket anticipated: the *binary* itself is theater, because it is right for the wrong reason. |

## 1. A substrate fact that shapes everything below

archy's graph is **internal-only**. `graph._external_target` collapses an external dotted path to its top-level package and `assemble_graph` keeps only internal edges. But every pattern in the book is a claim about the boundary between internal code and *external* infrastructure (the ORM, the web framework, the broker).

So none of this is derivable from `archy_graph` as it exists. Every detector below had to drop one layer down, to `parse_project`'s `ParseResult.imports`, to see `sqlalchemy` at all. Any `archy_patterns` surface would therefore not be a thin read over the existing graph; it would need a new external-import derivation carried up through the graph, the index, and the cache. That raises the bar the evidence has to clear, and the evidence does not clear it.

## 2. Detectors, chosen to span the inference-to-declaration axis

| detector | how "domain" is decided | inference or declaration |
| --- | --- | --- |
| **D_purity** | modules importing nothing non-stdlib | pure inference |
| **D_naming** | qualname carries `domain`/`model`/`entities`/... | convention |
| **D_position** | internal graph sinks (fan-in > 0, fan-out == 0) | pure inference, structural |
| **D_declared** | the user names the domain package | declaration (**what archy ships**) |

`D_position` is the interesting one and deserves its rationale stated fairly: "everything depends on it, it depends on nothing" is a genuine reading of the hexagonal ideal, and it is exactly the kind of thing a dependency graph *should* be able to see.

## 3. Result 1: the purity detector is tautological

`D_purity` defines the domain as modules importing nothing external, then asks whether those modules import infrastructure. **They cannot, by construction.** The bench confirms it: 0 flags across all 31 repos, including three controls whose domain classes literally subclass an ORM base.

Worth naming because the failure is not "weak signal" but "the question was defined out of existence". This is the same shape as the gate tautology caught in [#298] §5, and it is a reminder that a detector's definition can quietly guarantee its own answer.

## 4. Result 2: the position detector is right for the wrong reason

This is the finding the ticket was really asking for.

At repo level `D_position` looks like a clean win:

| corpus | flagged |
| --- | --- |
| A (patterns present, n=8) | **0/8** |
| B (patterns absent, n=3) | **3/3** |

Perfect separation. If the bench had stopped at repo-level verdicts, this would have shipped.

It does not survive looking at *which modules* it flagged. Domain-identification precision on the controls is **0.00, 0.00, 0.00**. It flagged:

| repo | D_position flagged | the actual domain |
| --- | --- | --- |
| microblog | `app.cli`, `app.search`, `app.translate` | `app.models` |
| flask-realworld | `conduit.commands`, `conduit.exceptions` | `conduit.*.models` |
| django-realworld | `conduit.apps.core.renderers`, `.utils` | `conduit.apps.*.models` |

CLI entrypoints, a translation adapter, exception modules, renderers. Every one is an **adapter**, and adapters are supposed to import infrastructure. The detector never once found the domain model it claims to be protecting.

What `D_position` actually measures is *"do this repo's leaf modules import a web framework"*, which is a proxy for **application vs library**, not for hexagonal conformance. The A-vs-B separation is real but it is separating the wrong populations: corpus A happens to be well-layered *and* library-shaped, corpus B is coupled *and* app-shaped, and the detector keys on the second half of each conjunction.

Corpus C is where that shows: `D_position` fires on **4/20 general Python projects** (fastapi 7/12 inferred-domain modules flagged, boto3 5/8, plus scrapy and datasette). None of these has a domain model that could violate a hexagonal boundary. That is the discriminant-validity failure `AXIS_REVIEW.md` requires a candidate to survive, and it is the productivity-theater failure mode [#142] warns about, arriving in a form the ticket did not anticipate: **the binary is theater too.** The ticket assumed a binary "you violate hexagonal in 3 places" would be actionable where a "hexagonal score: 0.72" would not. It would not, because the 3 places are wrong.

## 5. Result 3: the naming detector is a convention check with a fatal blind spot

`D_naming` has the best domain precision on the controls (1.00, 1.00, 0.38), which makes sense: `models.py` usually is the domain in a Django/Flask app. But:

- **It is not a structural signal.** It reads names. A project that calls its domain `core.py` is invisible to it, and 13 of 20 corpus-C projects have no domain-named module at all, so their "compliant" verdict is content-free (vacuous-pass rate **65%**).
- **Its one corpus-A flag is a false positive against the book's own architecture.** It flagged `djangoproject.alloc.models` in `appendix_django`, where that module is deliberately the Django ORM *adapter* and the pure domain lives in `allocation/domain/model.py`. It mistook an adapter named `models` for the domain.
- **It misses transitive coupling**, which is the case that matters most.

That last point is worth showing, because it is what decides the contracts question:

```
conduit/user/models.py:5   from conduit.database import Column, Model, SurrogatePK, db
conduit/database.py:3      from sqlalchemy.orm import relationship
```

`flask-realworld` is the most plainly active-record repo in the control set, and `D_naming` did not flag it, because the ORM coupling is laundered one hop through an internal module. Real coupling, invisible to a direct-import check.

## 6. Result 4: the contracts baseline sees exactly that case

Twelve lines of `.importlinter`, `include_external_packages = True`, run through the shipped `archy.contracts.run_contracts`:

```
all_kept: False | kept: 0 | broken: 1
contract: Domain models must not depend on the ORM or the web framework | kept: False
  chain: conduit.articles.models -> conduit.database (line 8) -> sqlalchemy (line 3)
  chain: conduit.profile.models  -> conduit.database (line 3) -> sqlalchemy (line 3)
  chain: conduit.user.models     -> conduit.database (line 5) -> sqlalchemy (line 3)
```

All three domain models, the full transitive chain, the line numbers to fix. The hardest case in the control set, handled today, by a feature archy already ships.

**The contracts delta is negative.** Declaration beats inference here on precision (1.00 by construction, because the user supplies the intent), on transitivity (contracts traverse, direct-import detectors do not), and on remediation (chains and line numbers versus a module name). The only thing inference would buy is not having to write the twelve lines, and it cannot even buy that correctly.

## 7. Why inference fails, stated generally

The three detectors are not three tuning attempts at one idea; they are the three available strategies, and each fails in its own characteristic way:

| strategy | failure |
| --- | --- |
| define domain by **purity** | violation impossible by construction |
| define domain by **name** | not structural; vacuous where the convention is absent; fooled by adapters named `models` |
| define domain by **graph position** | finds leaf adapters, not the domain; keys on app-vs-library |

The common cause: **"domain" is a statement of intent, and the import graph does not carry intent.** `model.py` and `renderers.py` can occupy identical graph positions. What makes one the domain is a human decision about what the software is *about*. Contracts work precisely because the user supplies that decision; every inferred detector has to guess it, and the guess is where all the error comes from.

This is a general result about the ticket's framing, not a limitation of these three implementations.

## 8. The rest of the pattern set

The ticket ranked five candidates by plausible graph-detectability. With the above in hand:

| # | pattern | verdict |
| --- | --- | --- |
| 1 | **Hexagonal / dependency direction** | Detectable **only as a declared contract**, which archy ships. Inference fails (§3-§7). |
| 2 | **Repository** | Same shape as #1 and same outcome: "only the repo module imports the ORM" is a Forbidden contract with the repo module whitelisted. Identifying *which* module is the repository has the same intent problem. |
| 3 | **Service Layer** | Needs to distinguish "thin ring of entrypoints" from "any module with moderate betweenness". Corpus A's service layer is not separable by position from corpus C's utility modules; this is §4's failure with an extra step. |
| 4 | **Message Bus / Handler** | Detectable only as the *absence* of direct edges plus a central dispatcher. Absence of an edge is unfalsifiable: it is also exactly what two unrelated modules look like. No detector was built; the design cannot produce a negative. |
| 5 | **Unit of Work / Aggregate / CQRS / DI** | Claims about runtime semantics, object identity, and transaction boundaries. Not visible to static imports at all. Recorded so it is not re-proposed. |

## 9. Honest limitations

- **Corpus B is n=3.** Three framework-coupled apps is a small control set. The 0.00 domain precision is consistent across all three and the mechanism is legible, but a wider control set could refine the rate.
- **Corpus A is n=8 of 11 branches.** The book's first chapters lay modules out as flat root-level `.py` files with no package, so archy finds no package root and returns zero modules. Those chapters contain no infra to violate against, so the loss does not affect the conclusion, but it is a real gap in coverage.
- **All corpus A and B repos are small** (13 to 44 modules). Cosmic's are teaching repos by design.
- **Ground truth is single-labeler.** I labeled the domain modules by hand from each repo's structure.
- **`INFRA_PACKAGES` is a curated list.** Deliberately not "anything third-party", since a domain model importing `attrs` is not an architectural violation, but the curation is a judgment call that a different curator would make differently at the margin.

None of these limitations point toward a positive result: the two decisive findings (0.00 domain precision, and contracts already handling the transitive case) are mechanism-level, not sample-size-level.

## 10. Disposition

| Item | Call |
| --- | --- |
| `archy_patterns` surface, in any form | **Wontfix.** §7 is a general argument, not a tuning gap. |
| Hexagonal / dependency-direction detector | **Wontfix as inference.** Already shipped as a declared contract. |
| Repository / Service Layer detectors | **Wontfix**, same failure mode (§8). |
| Message Bus / UoW / Aggregate / CQRS / DI | **Not graph-detectable.** Recorded in §8 so it is not re-proposed. |
| Docs | Worth one line in the contracts docs noting that `include_external_packages = True` is how you express "the domain must not import the ORM", since that is the shipped answer to the question this ticket asked. |

**Reopen path.** A positive result would need something that supplies intent without the user writing it down: inferring the domain from a source archy does not read today (docstrings, directory conventions declared in `pyproject.toml`, or an LLM labeling pass). Each of those is a different project from "detect patterns from the dependency graph", and each would have to beat the twelve-line contract in §6 on precision, transitivity, and remediation before it was worth shipping.

## Related

- [#124] constraint-conformance / surprise-rate signal
- [#139] rule rot / constraint staleness
- [#271] intended-vs-actual conformance score, the declared-intent framing that does work
- [#298] the framework-integration non-result, whose §5 tautology has the same shape as §3 here
- [`AXIS_REVIEW.md`](AXIS_REVIEW.md) for the OECD discriminant-validity check applied in §4
- [`../../bench/pattern_detection_results.md`](../../bench/pattern_detection_results.md) for the full run

[cosmic]: https://www.cosmicpython.com/book/preface.html
[#124]: https://github.com/hslee16/Archy/issues/124
[#139]: https://github.com/hslee16/Archy/issues/139
[#142]: https://github.com/hslee16/Archy/issues/142
[#271]: https://github.com/hslee16/Archy/issues/271
[#288]: https://github.com/hslee16/Archy/issues/288
[#298]: https://github.com/hslee16/Archy/issues/298
