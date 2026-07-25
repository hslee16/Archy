# Pattern detection bench: results

Harness: `bench/pattern_detection.py`. Question: #288, can archy *infer*
Cosmic Python's architectural patterns from the dependency graph, or is
"pattern detection" either redundant with contracts or undetectable without
declared intent?

Run 2026-07-24. `uv run --extra contracts python bench/pattern_detection.py`.

## Corpora

| corpus | what | n analyzed |
| --- | --- | --- |
| **A** patterns present | `cosmicpython/code`, branch per chapter | 8 |
| **B** patterns absent | framework-coupled Flask/Django apps (active-record) | 3 |
| **C** not applicable | general Python projects from `projects.yaml` | 20 |

Corpus A analyzed 8 of 11 branches. `chapter_01_domain_model`,
`chapter_02_repository`, and `chapter_04_service_layer` lay their modules out
as flat `.py` files at the repo root with no package, so archy's
`_find_package_roots` finds no package root and `parse_project` returns zero
modules. Those chapters also contain no infra to violate against, so the loss
is not material to the question.

## Headline table

Repo-level "flagged" = the detector reported at least one violation.
"Vacuous" = the detector inferred an empty domain set, so its "compliant"
verdict is content-free.

| detector | A flagged (want 0) | B flagged (want 3) | C flagged = **false positives** | C vacuous |
| --- | --- | --- | --- | --- |
| **D_purity** | 0/8 | **0/3** | 0/20 | 0/20 |
| **D_naming** | 1/8 | 2/3 | 2/20 | **13/20** |
| **D_position** | **0/8** | **3/3** | **4/20** | 0/20 |

Read alone, `D_position` looks like a clean win: perfect separation of A from
B. The next table is why it is not.

## Domain identification: does the detector find the domain at all?

Ground truth hand-labeled per repo (the modules that *are* the domain model).
Precision = inferred domain modules that are really domain.

| repo | corpus | D_purity | D_naming | D_position |
| --- | --- | --- | --- | --- |
| cosmic ch06 uow | A | 0.11 | 0.50 | 0.50 |
| cosmic ch07 aggregate | A | 0.11 | 0.50 | 0.50 |
| cosmic ch13 DI | A | 0.20 | 0.75 | 0.67 |
| microblog | B | 0.00 | 1.00 | **0.00** |
| flask-realworld | B | 0.12 | 1.00 | **0.00** |
| django-realworld | B | 0.00 | 0.38 | **0.00** |

`D_position` scores **0.00 on every control repo**. It flagged all three, and
in none of them did it identify the actual domain module. What it flagged
instead:

| repo | D_position flagged | the actual domain |
| --- | --- | --- |
| microblog | `app.cli`, `app.search`, `app.translate` | `app.models` |
| flask-realworld | `conduit.commands`, `conduit.exceptions` | `conduit.*.models` |
| django-realworld | `conduit.apps.core.renderers`, `.utils` | `conduit.apps.*.models` |

CLI entrypoints, translation adapters, exception modules, and renderers. Every
one is an adapter, and adapters are *supposed* to import infrastructure. The
detector gets the repo-level verdict right for entirely the wrong reason.

## Corpus C: what fires on projects with no domain at all

| detector | projects flagged | worst offenders |
| --- | --- | --- |
| D_position | 4/20 | fastapi 7/12, boto3 5/8, scrapy 1/14, datasette 1/12 |
| D_naming | 2/20 | scrapy 2/25, boto3 1/1 |

These are pure false positives. `fastapi` and `boto3` do not have a domain
model that could violate a hexagonal boundary. `D_position` calling 7 of
fastapi's 12 inferred "domain" modules violations is the discriminant-validity
failure `AXIS_REVIEW.md` requires a candidate to survive.

`D_naming`'s 13/20 vacuous rate is the mirror failure: no module happens to be
named `model`/`domain`, so the verdict is "compliant" with nothing checked.

## D_purity is tautological

`D_purity` defines domain as "imports nothing non-stdlib", then asks whether
those modules import infrastructure. They cannot, by construction. **0 flags
across all 31 repos**, including the three controls whose domain is literally
an ORM subclass. The separation is not weak, it is structurally impossible.
This is the pattern-detection analogue of the tautology caught in #298 §5.

## The false negative that matters

`D_naming` missed `flask-realworld` at repo level despite it being the most
plainly active-record repo in the control set. Reason:

```
conduit/user/models.py:5   from conduit.database import Column, Model, SurrogatePK, db
conduit/database.py:3      from sqlalchemy.orm import relationship
```

The ORM coupling is real but **transitive**, laundered through an internal
module. A direct-import detector cannot see it.

## The contracts baseline sees exactly that case

12 lines of `.importlinter` with `include_external_packages = True`, run
through `archy.contracts.run_contracts`:

```
all_kept: False | kept: 0 | broken: 1
contract: Domain models must not depend on the ORM or the web framework | kept: False
  chain: conduit.articles.models -> conduit.database (line 8) -> sqlalchemy (line 3)
  chain: conduit.profile.models  -> conduit.database (line 3) -> sqlalchemy (line 3)
  chain: conduit.user.models     -> conduit.database (line 5) -> sqlalchemy (line 3)
```

All three domain models, the full transitive chain, and the line numbers to
fix. This is the case every inferred detector missed, and archy ships it today.

## Also worth recording

`D_naming`'s single corpus-A flag is `djangoproject.alloc.models` on
`appendix_django`. In the book that module is deliberately the **Django ORM
adapter**, with the pure domain living in `allocation/domain/model.py`. So the
detector's one "hit" on the patterns-present corpus is a false positive against
the book's own architecture: it mistook an adapter named `models` for the
domain.

## Verdict

The ticket's kill criterion: *if the top candidate cannot separate
patterns-present from patterns-absent repos better than the existing contracts
feature already does, close as a non-result.*

Contracts separate them exactly, with remediation chains, on the hardest case.
The best inferred detector separates them at repo level with 0.00 domain
precision and a 20% false-positive rate on projects where the question does not
apply. **NO-GO.**
