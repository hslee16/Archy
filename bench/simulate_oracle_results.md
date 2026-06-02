# archy_simulate validation (adversarial)

Samples: 327 (308 clean, 19 dirty).
**Fidelity (clean rate): 308/327 = 94%** -- intended single-edge delta maps 1:1 to the written import.
**Oracle on clean samples: 308/308 matched** (0 bug-level mismatches). This is the real correctness gate.
Oracle on dirty samples: 0/19 matched -- i.e. simulate diverged from the written edit on 19 of them (the resolved-edge caveat, quantified).
Overall agent-facing match: 308/327 = 94%.
Complexity-axis nonzero on an edge delta (must be 0): 0.
simulate vs diff wall-clock: 1.22x (corpus <= 174 modules).

| repo | mods | edges | rm clean/tot | rm match(clean) | add clean/tot | add match(clean) | add->cycle | add->back |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| scrapy | 174 | 869 | 13/15 | 13/13 | 15/15 | 15/15 | 5 | 3 |
| pydantic | 104 | 496 | 13/15 | 13/13 | 15/15 | 15/15 | 3 | 1 |
| rich | 100 | 421 | 14/15 | 14/14 | 12/15 | 12/12 | 2 | 2 |
| datasette | 68 | 213 | 13/15 | 13/13 | 10/15 | 10/10 | 3 | 2 |
| mkdocs | 61 | 177 | 13/15 | 13/13 | 15/15 | 15/15 | 3 | 0 |
| fastapi | 48 | 114 | 15/15 | 15/15 | 15/15 | 15/15 | 0 | 2 |
| starlette | 34 | 115 | 15/15 | 15/15 | 15/15 | 15/15 | 4 | 3 |
| flask | 24 | 94 | 14/15 | 14/14 | 15/15 | 15/15 | 5 | 8 |
| httpx | 23 | 87 | 15/15 | 15/15 | 15/15 | 15/15 | 6 | 5 |
| requests | 19 | 73 | 14/15 | 14/14 | 15/15 | 15/15 | 5 | 3 |
| click | 17 | 61 | 15/15 | 15/15 | 12/12 | 12/12 | 4 | 4 |

## Dirty-sample characterization (why intended != written)
- rm datasette.app->datasette.utils.baseconv: real touched [('datasette.app', 'datasette.utils'), ('datasette.app', 'datasette.utils.baseconv')]
- rm datasette.views.stored_queries->datasette.utils.sqlite: real touched [('datasette.views.stored_queries', 'datasette.utils'), ('datasette.views.stored_queries', 'datasette.utils.sqlite')]
- add datasette.actor_auth_cookie->datasette.default_permissions.config: real touched [('datasette.actor_auth_cookie', 'datasette'), ('datasette.actor_auth_cookie', 'datasette.default_permissions.config')]
- add datasette.default_debug_menu->datasette.views.stored_queries: real touched [('datasette.default_debug_menu', 'datasette'), ('datasette.default_debug_menu', 'datasette.views.stored_queries')]
- rm flask->flask.app: real touched [('flask', 'flask.app')]
- rm mkdocs.__main__->mkdocs.commands.build: real touched [('mkdocs.__main__', 'mkdocs.commands.build'), ('mkdocs.__main__', 'mkdocs.commands.gh_deploy')]
- rm mkdocs.tests.config.config_options_legacy_tests->mkdocs.utils.yaml: real touched [('mkdocs.tests.config.config_options_legacy_tests', 'mkdocs.utils'), ('mkdocs.tests.config.config_options_legacy_tests', 'mkdocs.utils.yaml')]
- rm pydantic._internal._fields->pydantic._internal._generics: real touched [('pydantic._internal._fields', 'pydantic._internal._generics'), ('pydantic._internal._fields', 'pydantic._internal._typing_extra')]
- rm pydantic.type_adapter->pydantic._internal._utils: real touched [('pydantic.type_adapter', 'pydantic._internal._config'), ('pydantic.type_adapter', 'pydantic._internal._generate_schema'), ('pydantic.type_adapter', 'pydantic._internal._mock_val_ser')]
- rm requests->requests.packages: real touched [('requests', 'requests.packages'), ('requests', 'requests.utils')]
- rm rich.table->rich.box: real touched [('rich.table', 'rich.box'), ('rich.table', 'rich.errors')]
- add rich._palettes->rich._unicode_data.unicode8-0-0: real touched [('rich._palettes', 'rich._unicode_data')]

## Scale + perf (synthetic graphs, closes the corpus gap)

| nodes | simulate | diff | ratio | cycles_added |
|--:|--:|--:|--:|--:|
| 500 | 0.21s | 0.17s | 1.24x | 1 |
| 2000 | 1.36s | 1.05s | 1.29x | 1 |
| 5000 | 5.23s | 4.33s | 1.21x | 1 |
| 10000 | 17.39s | 15.72s | 1.11x | 1 |

simulate's overhead over a diff stays ~constant at scale (the extra DSM + propagation passes are cheap next to the shared snapshot cost); absolute latency grows with the snapshot work, not with simulate.

## Layer-violation smoke (synthetic, 4 layers, forbid l0->l1)
- forbidden edge flagged: True
- allowed edge stays silent: True

## Gaps
- Violation prediction reuses archy's own find_violations on the hypothetical graph; covered by the synthetic smoke above and unit tests, not by real-repo layer rules (the corpus carries no archy.yaml).
