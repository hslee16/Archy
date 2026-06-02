# archy_simulate validation (adversarial)

Samples: 327 (315 clean, 12 dirty).
**Fidelity (clean rate): 315/327 = 96%** -- intended single-edge delta maps 1:1 to the written import.
**Oracle on clean samples: 315/315 matched** (0 bug-level mismatches). This is the real correctness gate.
Oracle on dirty samples: 0/12 matched -- i.e. simulate diverged from the written edit on 12 of them (the resolved-edge caveat, quantified).
Overall agent-facing match: 315/327 = 96%.
Complexity-axis nonzero on an edge delta (must be 0): 0.
simulate vs diff wall-clock: 1.23x (corpus <= 174 modules).

| repo | mods | edges | rm clean/tot | rm match(clean) | add clean/tot | add match(clean) | add->cycle | add->back |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| scrapy | 174 | 869 | 13/15 | 13/13 | 15/15 | 15/15 | 5 | 3 |
| pydantic | 104 | 496 | 13/15 | 13/13 | 15/15 | 15/15 | 3 | 1 |
| rich | 100 | 421 | 14/15 | 14/14 | 15/15 | 15/15 | 3 | 3 |
| datasette | 68 | 213 | 13/15 | 13/13 | 15/15 | 15/15 | 3 | 2 |
| mkdocs | 61 | 177 | 13/15 | 13/13 | 15/15 | 15/15 | 3 | 0 |
| fastapi | 48 | 114 | 15/15 | 15/15 | 15/15 | 15/15 | 0 | 2 |
| starlette | 34 | 115 | 15/15 | 15/15 | 15/15 | 15/15 | 4 | 3 |
| flask | 24 | 94 | 14/15 | 14/14 | 14/15 | 14/14 | 5 | 8 |
| httpx | 23 | 87 | 15/15 | 15/15 | 15/15 | 15/15 | 6 | 5 |
| requests | 19 | 73 | 14/15 | 14/14 | 15/15 | 15/15 | 5 | 3 |
| click | 17 | 61 | 15/15 | 15/15 | 12/12 | 12/12 | 4 | 4 |

## Dirty-sample characterization (why intended != written)
- rm datasette.app->datasette.utils.baseconv: real touched [('datasette.app', 'datasette.utils'), ('datasette.app', 'datasette.utils.baseconv')]
- rm datasette.views.stored_queries->datasette.utils.sqlite: real touched [('datasette.views.stored_queries', 'datasette.utils'), ('datasette.views.stored_queries', 'datasette.utils.sqlite')]
- rm flask->flask.app: real touched [('flask', 'flask.app')]
- add flask.json.tag->flask.sansio.blueprints: real touched [('flask.json.tag', 'flask')]
- rm mkdocs.__main__->mkdocs.commands.build: real touched [('mkdocs.__main__', 'mkdocs.commands.build'), ('mkdocs.__main__', 'mkdocs.commands.gh_deploy')]
- rm mkdocs.tests.config.config_options_legacy_tests->mkdocs.utils.yaml: real touched [('mkdocs.tests.config.config_options_legacy_tests', 'mkdocs.utils'), ('mkdocs.tests.config.config_options_legacy_tests', 'mkdocs.utils.yaml')]
- rm pydantic._internal._fields->pydantic._internal._generics: real touched [('pydantic._internal._fields', 'pydantic._internal._generics'), ('pydantic._internal._fields', 'pydantic._internal._typing_extra')]
- rm pydantic.type_adapter->pydantic._internal._utils: real touched [('pydantic.type_adapter', 'pydantic._internal._config'), ('pydantic.type_adapter', 'pydantic._internal._generate_schema'), ('pydantic.type_adapter', 'pydantic._internal._mock_val_ser')]
- rm requests->requests.packages: real touched [('requests', 'requests.packages'), ('requests', 'requests.utils')]
- rm rich.table->rich.box: real touched [('rich.table', 'rich.box'), ('rich.table', 'rich.errors')]
- rm scrapy->scrapy.http.request: real touched [('scrapy', 'scrapy.http.request'), ('scrapy.core.downloader', 'scrapy.http.request'), ('scrapy.core.downloader.handlers', 'scrapy.http.request')]
- rm scrapy.utils.iterators->scrapy.http.response.text: real touched [('scrapy.utils.iterators', 'scrapy.http.response'), ('scrapy.utils.iterators', 'scrapy.http.response.text')]

## Scale + perf (synthetic graphs, closes the corpus gap)

| nodes | simulate | diff | ratio | cycles_added |
|--:|--:|--:|--:|--:|
| 500 | 0.21s | 0.17s | 1.23x | 1 |
| 2000 | 1.24s | 0.97s | 1.28x | 1 |
| 5000 | 4.99s | 3.94s | 1.27x | 1 |
| 10000 | 16.83s | 14.12s | 1.19x | 1 |

simulate's overhead over a diff stays ~constant at scale (the extra DSM + propagation passes are cheap next to the shared snapshot cost); absolute latency grows with the snapshot work, not with simulate.

## Layer-violation smoke (synthetic, 4 layers, forbid l0->l1)
- forbidden edge flagged: True
- allowed edge stays silent: True

## Gaps
- Violation prediction reuses archy's own find_violations on the hypothetical graph; covered by the synthetic smoke above and unit tests, not by real-repo layer rules (the corpus carries no archy.yaml).
