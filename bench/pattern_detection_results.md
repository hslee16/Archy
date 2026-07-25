# Pattern detection bench: results

Harness: `bench/pattern_detection.py`. Question: #288, can archy *infer*
Cosmic Python's architectural patterns from the dependency graph, or is
"pattern detection" either redundant with contracts or undetectable without
declared intent?

Run 2026-07-24. `uv run --extra contracts python bench/pattern_detection.py`.

> **This file records the second run.** The first run's writeup was refuted on
> three points by an adversarial review and is not preserved: it used a wrong
> ground-truth label for `django-realworld`, counted self-stack dependencies
> (fastapi importing starlette) as false positives, and silently analyzed 20 of
> 29 corpus-C projects. All three are fixed here. The corrections are listed in
> the last section, because a bench that quietly revises its own numbers is
> worth less than one that shows the revision.

## Corpora

| corpus | what | n analyzed |
| --- | --- | --- |
| **A** patterns present | `cosmicpython/code`, branch per chapter | 8 of 11 |
| **B** patterns absent | framework-coupled Flask/Django apps (active-record) | 3 |
| **C** not applicable | `projects.yaml` general Python projects | **29 of 29** |

Corpus A analyzed 8 of 11 branches. `chapter_01_domain_model`,
`chapter_02_repository`, and `chapter_04_service_layer` lay their modules out
as flat `.py` files at the repo root with no package, so archy's
`_find_package_roots` finds no package root and `parse_project` returns zero
modules. Those chapters also contain no infra to violate against.

## Repo-level verdicts

"Flagged" = the detector reported at least one violation. "Vacuous" = the
detector inferred an empty domain set, so its "compliant" verdict is
content-free.

| detector | A flagged (want 0) | B flagged (want 3) | C flagged (want 0) | C vacuous |
| --- | --- | --- | --- | --- |
| **D_purity** | 0/8 | **0/3** | 0/29 | 0/29 |
| **D_naming** | 1/8 | 2/3 | 3/29 | **15/29** |
| **D_position** | **0/8** | **3/3** | 4/29 | 0/29 |
| **D_position_relaxed** | **0/8** | **3/3** | 5/29 | 0/29 |

Read alone, both positional detectors look like clean wins on A vs B. The next
table is why they are not.

## Domain identification: does the detector find the domain at all?

Precision = inferred domain modules that are really domain. Recall = true
domain modules found. Ground truth is hand-labeled in `GROUND_TRUTH`.

| repo | corpus | D_purity | D_naming | D_position | D_position_relaxed |
| --- | --- | --- | --- | --- | --- |
| cosmic ch06 uow | A | 0.11/1.00 | 0.50/1.00 | 0.50/1.00 | 0.50/1.00 |
| cosmic ch07 aggregate | A | 0.11/1.00 | 0.50/1.00 | 0.50/1.00 | 0.50/1.00 |
| cosmic ch08 events | A | 0.17/1.00 | 0.67/1.00 | 0.33/0.50 | 0.50/1.00 |
| cosmic ch13 DI | A | 0.20/1.00 | 0.75/1.00 | 0.67/0.67 | 0.75/1.00 |
| microblog | B | 0.00/0.00 | 1.00/1.00 | **0.00/0.00** | **0.00/0.00** |
| django-realworld | B | 0.00/0.00 | 0.50/1.00 | 0.33/0.25 | 0.22/1.00 |
| flask-realworld | B | 0.12/0.33 | 1.00/1.00 | **0.00/0.00** | **0.00/0.00** |

**On two of the three controls, both positional detectors identify zero true
domain modules while still flagging the repo.** What `D_position` flagged
instead:

| repo | D_position flagged | the actual domain |
| --- | --- | --- |
| microblog | `app.cli`, `app.search`, `app.translate` | `app.models` |
| flask-realworld | `conduit.commands`, `conduit.exceptions` | `conduit.*.models` |
| django-realworld | `conduit.apps.core.models`, `.renderers` | `conduit.apps.*.models` |

The microblog and flask-realworld flags are CLI entrypoints, a translation
adapter, and an exception module: adapters, which are *supposed* to import
infrastructure. The django-realworld case is different and is a genuine hit:
`conduit.apps.core.models` holds `TimestampedModel`, the abstract Django base
that all three concrete domain models inherit from, so the detector found real
ORM-in-the-domain coupling there. That is why its precision is 0.33 and not
0.00.

So the honest summary is not "it never finds the domain". It is: **it finds the
domain in one control out of three, and in the other two it produces the
correct repo-level verdict entirely from adapters.**

## The steelman does not rescue positional inference

A strict sink is a demanding definition. In cosmic, `allocation.domain.model`
is not a sink at all, because it imports `allocation.domain.events`.
`D_position_relaxed` therefore asks only that a module's internal dependencies
stay inside its own top-2-level package.

It does help recall where it helps at all: on `django-realworld` it finds all
four labeled domain modules (recall 1.00, up from 0.25). But precision drops to
0.22, it stays at **0.00/0.00 on the other two controls**, and it flags *more*
corpus-C projects (5/29 vs 4/29). Relaxing the definition trades one failure
for another rather than fixing it.

Two operationalizations is not a proof that no positional detector can work.
It is enough to say the failure is not an artifact of picking a strict sink.

## Corpus C: the detector cannot tell "not applicable" from "violating"

After subtracting each project's own foundation dependency (see corrections
below), `D_position` still emits violations on 4 of 29 projects that have no
domain/infrastructure split at all:

| project | violations | example |
| --- | --- | --- |
| flask | 1/2 | `flask.typing` |
| django | 1/40 | `django.db.backends.postgresql.psycopg_any` |
| home-assistant | 15/843 | `homeassistant.components.denonavr.receiver` |
| dagster | 3/38 | `dagster._core.storage.sqlalchemy_compat` |

The per-module rates are low, and every one of these is a genuinely
infra-adjacent module. That is precisely the problem: they are correct
observations about imports and meaningless as architecture findings, because
none of these projects has a hexagon to violate. A shipped detector would tell
a Django maintainer that `django.db.backends.postgresql` violates hexagonal
architecture.

`D_naming`'s failure here is the mirror image: **15 of 29 vacuous**. No module
happens to be named `model`/`domain`, so the verdict is "compliant" with
nothing checked.

## D_purity is tautological

`D_purity` defines domain as "imports nothing non-stdlib", then asks whether
those modules import infrastructure. They cannot, by construction. **0 flags
across all 40 repos**, including the three controls whose domain classes
subclass an ORM base. The separation is not weak, it is structurally
impossible. Same shape as the tautology caught in #298 §5.

## The false negative that decides the contracts question

`D_naming` did not flag `flask-realworld`, the most plainly active-record repo
in the control set, despite identifying its domain perfectly (precision 1.00,
recall 1.00). Reason:

```
conduit/user/models.py:5   from conduit.database import Column, Model, SurrogatePK, db
conduit/database.py:3      from sqlalchemy.orm import relationship
```

The ORM coupling is real but **transitive**, laundered one hop through an
internal module. A direct-import detector cannot see it.

## The contracts baseline, and its asymmetry

14 lines of `.importlinter` (committed at `bench/fixtures/flask_realworld.importlinter`)
with `include_external_packages = True`, through the shipped
`archy.contracts.run_contracts`:

```
all_kept: False | kept: 0 | broken: 1
contract: Domain models must not depend on the ORM or the web framework | kept: False
  chain: conduit.articles.models -> conduit.database (line 8) -> sqlalchemy (line 3)
  chain: conduit.profile.models  -> conduit.database (line 3) -> sqlalchemy (line 3)
  chain: conduit.user.models     -> conduit.database (line 5) -> sqlalchemy (line 3)
```

All three domain models, the full transitive chain, the line numbers to fix.

**Scope this honestly.** This was run on **one repo**, by hand, because
import-linter builds its graph by importing the package and so needs each
project's runtime dependencies installed. It was not run on corpus A, on the
other two controls, or on any corpus-C project. And the contract is *handed*
the three domain module names that every inferred detector had to guess.

So the correct claim is narrow: **on `flask-realworld`, contracts caught the
transitive coupling that all four inferred detectors missed, and did so with
remediation chains.** On that same repo `D_naming` identified the domain just
as well (1.00/1.00). The axis contracts win on is **transitivity**, not
everything.

## Also worth recording

`D_naming`'s single corpus-A flag is `djangoproject.alloc.models` on
`appendix_django`. In the book that module is deliberately the **Django ORM
adapter**, with the pure domain in `allocation/domain/model.py`. Its one "hit"
on the patterns-present corpus is a false positive against the book's own
architecture: it mistook an adapter named `models` for the domain.

## Verdict

The ticket's kill criterion: *if the top candidate cannot separate
patterns-present from patterns-absent repos better than the existing contracts
feature already does, close as a non-result.*

No inferred detector is shippable. The positional pair produce correct
repo-level verdicts from the wrong modules on 2 of 3 controls and cannot tell
an inapplicable project from a violating one. `D_naming` has real domain
precision but is a naming convention, is vacuous on half the general corpus,
and is blind to the transitive case that matters most. **NO-GO.**

## Corrections applied after adversarial review

| # | first-run claim | what was wrong | fix |
| --- | --- | --- | --- |
| 1 | `D_position` precision 0.00 on all three controls; "never once found the domain"; flagged `.utils` | `conduit.apps.core.models` (the abstract ORM base of all three domain models) was missing from the ground truth and *was* flagged; `.utils` was never flagged | `GROUND_TRUTH` moved into the harness and corrected; precision is 0.33 there |
| 2 | 4/20 corpus-C false positives = discriminant-validity failure | all 13 violation instances were self-stack deps (fastapi→starlette, boto3/scrapy→botocore) or the `sqlite3` stdlib carve-out | `SELF_STACK` subtraction added; those four projects now flag 0 |
| 3 | "Contracts separate them exactly", "inference loses on every axis measured" | one repo, run by hand, with the domain names handed over; `D_naming` matched its domain precision there | claim scoped to transitivity on one repo; fixture committed |
| 4 | corpus C = 20 projects | 9 broken local clones silently dropped, disproportionately the large app-shaped ones | clones repaired, now **29/29**, coverage printed by the harness |
| 5 | only strict-sink positional inference tested | the negative was a property of one operationalization | `D_position_relaxed` added |
| 6 | test filter dropped any part starting with `test` | also dropped shipped API (`fastapi.testclient`) | exact-part match against `TEST_PARTS` |
