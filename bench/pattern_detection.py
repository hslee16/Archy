"""Bench experiment: can archy detect architectural patterns from the graph?

The empirical half of #288. The question is whether "architectural pattern
detection" (Cosmic Python's Repository / Service Layer / Hexagonal set) is a
real low-false-positive structural signal archy can *infer*, or whether it is
either (a) redundant with the contracts feature archy already ships or (b)
undetectable without the user declaring intent.

Three corpora, because a detector has to do three different things:

  A. patterns PRESENT   -- cosmicpython/code, branch per chapter. The book's
                           own reference implementations.
  B. patterns ABSENT    -- framework-coupled Flask/Django apps (active-record
                           models, ORM imported straight into the domain).
  C. NOT APPLICABLE     -- the general Python projects in `projects.yaml`.
                           Libraries with no domain/infra split at all. This is
                           the applicability corpus: a detector that emits
                           violations here is claiming a hexagonal breach in a
                           project that has no hexagon, which is the OECD
                           discriminant check AXIS_REVIEW.md requires and the
                           failure mode #142 warns about.

Four INFERRED detectors, spanning how "domain" might be guessed:

  D_purity           -- domain := internal modules importing nothing non-stdlib.
                        Violation := such a module importing an infra package.
  D_naming           -- domain := qualname carries a domain-ish token
                        (model/domain/entities/...). Violation := infra import.
  D_position         -- domain := graph sinks (internal fan-in > 0, fan-out ==
                        0), the "everything depends on it, it depends on
                        nothing" shape of the hexagonal ideal.
  D_position_relaxed -- the steelman for the above. A strict sink is demanding
                        (cosmic's `domain.model` is not one, it imports
                        `domain.events`), so this asks only that every internal
                        dependency stay inside the module's own top-2-level
                        package. Present so a negative result is a property of
                        positional inference, not of one operationalization.

Each detector is scored two ways: the repo-level flagged/not-flagged verdict,
AND domain-identification precision against the hand-labeled GROUND_TRUTH
below. The second is the one that matters, because a detector can get every
repo-level verdict right while never once identifying the domain.

NOT implemented here: the contracts baseline (`archy_check(contracts=True)`
with `include_external_packages = True`), which is what archy ships today. It
requires an importable environment per project, so it was run by hand on one
control repo; see `pattern_detection_results.md`. Any claim comparing inference
to contracts is therefore scoped to that single repo, and contracts are handed
the domain module names rather than inferring them.

Note that archy's *graph* cannot answer this question on its own: external
imports are collapsed by `_external_target` and dropped by `assemble_graph`
(the graph is internal-only). Every detector below therefore reads
`parse_project`'s ParseResult imports, one layer below the graph.

Usage:
    uv run python bench/pattern_detection.py
    uv run python bench/pattern_detection.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import REPO_ROOT, clone_or_update, load_manifest

sys.path.insert(0, str(REPO_ROOT / "src"))
from archy.graph import DEFAULT_IGNORED_DIRS, assemble_graph, parse_project

WORKDIR = Path("/tmp/archy_pattern_bench")

# --- corpora ---------------------------------------------------------------

# A: the book's own code, branch per chapter. Ordered by the chapter in which
# each pattern is introduced, so a detector's behaviour can be read against the
# pattern actually present rather than against "cosmic python" as a monolith.
COSMIC_BRANCHES = [
    ("chapter_01_domain_model", "domain model only"),
    ("chapter_02_repository", "+ Repository"),
    ("chapter_04_service_layer", "+ Service Layer"),
    ("chapter_06_uow", "+ Unit of Work"),
    ("chapter_07_aggregate", "+ Aggregate"),
    ("chapter_08_events_and_message_bus", "+ Domain Events / Message Bus"),
    ("chapter_09_all_messagebus", "all-messagebus"),
    ("chapter_10_commands", "+ Commands"),
    ("chapter_12_cqrs", "+ CQRS"),
    ("chapter_13_dependency_injection", "+ DI / bootstrap"),
    ("appendix_django", "Django adapter (patterns present, ORM swapped in)"),
]

# B: framework-coupled controls. Chosen because the domain object IS the ORM
# model (active record), which is precisely the coupling the book forbids.
CONTROL_REPOS = [
    ("microblog", "miguelgrinberg/microblog", "Flask + SQLAlchemy active-record"),
    (
        "django-realworld",
        "gothinkster/django-realworld-example-app",
        "Django REST active-record",
    ),
    (
        "flask-realworld",
        "gothinkster/flask-realworld-example-app",
        "Flask + SQLAlchemy active-record",
    ),
]

# Infra = the things the book says the domain must not know about. Deliberately
# a curated list rather than "anything third-party": a domain model importing
# `attrs` or `dateutil` is not an architectural violation, and conflating the
# two is the fastest way to manufacture false positives.
INFRA_PACKAGES = frozenset(
    {
        # ORM / DB drivers
        "sqlalchemy",
        "django",
        "psycopg2",
        "psycopg",
        "pymysql",
        "sqlite3",
        "pymongo",
        "peewee",
        "tortoise",
        "asyncpg",
        "alembic",
        # web / transport
        "flask",
        "fastapi",
        "starlette",
        "requests",
        "httpx",
        "aiohttp",
        "werkzeug",
        "rest_framework",
        # brokers / caches / cloud
        "redis",
        "celery",
        "kombu",
        "boto3",
        "botocore",
        "pika",
        "elasticsearch",
    }
)

DOMAIN_NAME_TOKENS = frozenset({"domain", "model", "models", "entity", "entities", "core"})

TEST_PARTS = frozenset({"test", "tests", "testing", "conftest"})

# A project's own foundation dependency. Subtracted from its violations,
# because "fastapi imports starlette" is what fastapi IS, not a layering
# failure. Corpus-C only; the controls do not get this exemption.
SELF_STACK = {
    "fastapi": frozenset({"starlette"}),
    "boto3": frozenset({"botocore"}),
    "botocore": frozenset({"botocore"}),
    "scrapy": frozenset({"botocore"}),
    "datasette": frozenset({"sqlite3"}),
    "starlette": frozenset({"httpx"}),
    "httpx": frozenset({"httpx"}),
}

# Hand-labeled: the modules that ARE the domain model. Used to score whether a
# detector's inferred "domain" is the real one, which the repo-level flagged /
# not-flagged verdict cannot show.
#
# `conduit.apps.core.models` is included for django-realworld deliberately: it
# holds `TimestampedModel`, the abstract Django base every one of the three
# concrete domain models inherits from. Omitting it in the first run of this
# bench is what produced a spurious 0.00 precision for D_position there.
GROUND_TRUTH: dict[str, set[str]] = {
    "chapter_06_uow": {"allocation.domain.model"},
    "chapter_07_aggregate": {"allocation.domain.model"},
    "chapter_08_events_and_message_bus": {
        "allocation.domain.model",
        "allocation.domain.events",
    },
    "chapter_13_dependency_injection": {
        "allocation.domain.model",
        "allocation.domain.events",
        "allocation.domain.commands",
    },
    "microblog": {"app.models"},
    "flask-realworld": {
        "conduit.user.models",
        "conduit.profile.models",
        "conduit.articles.models",
    },
    "django-realworld": {
        "conduit.apps.articles.models",
        "conduit.apps.profiles.models",
        "conduit.apps.authentication.models",
        "conduit.apps.core.models",
    },
}


class RepoResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    corpus: str
    name: str
    label: str
    n_modules: int
    # per-detector: (domain_size, violations)
    purity_domain: int
    purity_violations: int
    naming_domain: int
    naming_violations: int
    position_domain: int
    position_violations: int
    relaxed_domain: int
    relaxed_violations: int
    infra_using_modules: int
    # Domain-identification scoring against GROUND_TRUTH (0 where unlabeled).
    truth_size: int = 0
    purity_tp: int = 0
    naming_tp: int = 0
    position_tp: int = 0
    relaxed_tp: int = 0
    position_flagged: tuple[str, ...] = ()


def _stdlib() -> frozenset[str]:
    return frozenset(sys.stdlib_module_names)


def _external_imports(parse_results: dict, internal: set[str]) -> dict[str, set[str]]:
    """Map module qualname -> set of top-level EXTERNAL packages it imports.

    Relative imports are internal by definition. An absolute import is internal
    if any dotted prefix resolves to a known internal module (mirroring
    `graph._external_target`'s resolution), otherwise its top-level name is the
    external package.
    """
    stdlib = _stdlib()
    out: dict[str, set[str]] = {}
    for qualname, result in parse_results.items():
        ext: set[str] = set()
        for ref in result.imports:
            if ref.is_relative:
                continue
            parts = ref.module.split(".")
            if any(".".join(parts[:end]) in internal for end in range(len(parts), 0, -1)):
                continue
            top = parts[0]
            if not top or (top in stdlib and top != "sqlite3"):
                continue
            ext.add(top)
        out[qualname] = ext
    return out


def _infra_of(ext: set[str]) -> set[str]:
    return {pkg for pkg in ext if pkg in INFRA_PACKAGES}


def _has_domain_token(qualname: str) -> bool:
    return any(part in DOMAIN_NAME_TOKENS for part in qualname.split("."))


def analyze(root: Path, corpus: str, name: str, label: str) -> RepoResult | None:
    try:
        modules, parse_results = parse_project(
            root, ignored_dirs=DEFAULT_IGNORED_DIRS, max_modules=0
        )
    except Exception:
        return None
    if not modules:
        return None
    internal = {m.qualname for m in modules}
    ext = _external_imports(parse_results, internal)
    # Tests are not the architecture. A test module importing sqlalchemy is
    # expected and would swamp every detector. Matched on exact path parts, NOT
    # `startswith("test")`, which would also drop shipped public API such as
    # `fastapi.testclient` / `starlette.testclient`.
    real = {q for q in ext if not any(p in TEST_PARTS for p in q.split("."))}
    ext = {q: v for q, v in ext.items() if q in real}

    graph = assemble_graph(root, modules, parse_results)
    internal_graph = nx.DiGraph()
    internal_graph.add_nodes_from(real)
    for src, dst in graph.edges():
        if src in real and dst in real:
            internal_graph.add_edge(src, dst)

    # A project's own foundation dependency is not "infrastructure leaking into
    # the domain": fastapi IS a layer over starlette, boto3 over botocore. Left
    # in, these produce violations that say nothing about layering, and they
    # accounted for every corpus-C flag in the first run of this bench.
    self_stack = SELF_STACK.get(name, frozenset())

    def _infra(q: str) -> set[str]:
        return _infra_of(ext[q]) - self_stack

    # D_purity: domain := imports nothing external at all.
    purity_domain = {q for q in real if not ext[q]}
    purity_violations = {q for q in purity_domain if _infra(q)}

    # D_naming: domain := carries a domain-ish name token.
    naming_domain = {q for q in real if _has_domain_token(q)}
    naming_violations = {q for q in naming_domain if _infra(q)}

    # D_position: domain := internal sink (imported by peers, imports no peer).
    position_domain = {
        q for q in real if internal_graph.in_degree(q) > 0 and internal_graph.out_degree(q) == 0
    }
    position_violations = {q for q in position_domain if _infra(q)}

    # D_position_relaxed: the steelman. A strict sink is a demanding definition
    # (in cosmic, `domain.model` is not one, because it imports `domain.events`).
    # This variant asks only that every internal dependency stay inside the
    # module's own top-2-level package, which is the "self-contained core"
    # reading of the hexagonal ideal. Included so the negative result is a
    # property of positional inference rather than of one operationalization.
    def _prefix(q: str) -> str:
        return ".".join(q.split(".")[:2])

    relaxed_domain = {
        q
        for q in real
        if internal_graph.in_degree(q) > 0
        and all(_prefix(s) == _prefix(q) for s in internal_graph.successors(q))
    }
    relaxed_violations = {q for q in relaxed_domain if _infra(q)}

    truth = GROUND_TRUTH.get(name)
    return RepoResult(
        corpus=corpus,
        name=name,
        label=label,
        n_modules=len(real),
        purity_domain=len(purity_domain),
        purity_violations=len(purity_violations),
        naming_domain=len(naming_domain),
        naming_violations=len(naming_violations),
        position_domain=len(position_domain),
        position_violations=len(position_violations),
        relaxed_domain=len(relaxed_domain),
        relaxed_violations=len(relaxed_violations),
        infra_using_modules=len([q for q in real if _infra(q)]),
        truth_size=len(truth) if truth else 0,
        purity_tp=len(purity_domain & truth) if truth else 0,
        naming_tp=len(naming_domain & truth) if truth else 0,
        position_tp=len(position_domain & truth) if truth else 0,
        relaxed_tp=len(relaxed_domain & truth) if truth else 0,
        position_flagged=tuple(sorted(q for q in position_domain if _infra(q))),
    )


def _git(args: list[str], cwd: Path | None = None) -> int:
    return subprocess.run(args, cwd=cwd, capture_output=True).returncode


def _prepare(name: str, repo: str, *, shallow: bool = True) -> Path | None:
    """Clone `repo` to WORKDIR/name if absent. Corpus A needs full history
    (the bench checks out one branch per chapter); the controls do not."""
    target = WORKDIR / name
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        depth = ["--depth", "1"] if shallow else []
        if _git(["git", "clone", "--quiet", *depth, f"https://github.com/{repo}.git", str(target)]):
            return None
    return target


def _print_row(prefix: str, label: str, r: RepoResult) -> None:
    print(
        f"{prefix} {label:42s} n={r.n_modules:3d} "
        f"purity={r.purity_violations}/{r.purity_domain} "
        f"naming={r.naming_violations}/{r.naming_domain} "
        f"position={r.position_violations}/{r.position_domain} "
        f"relaxed={r.relaxed_violations}/{r.relaxed_domain}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--skip-corpus-c", action="store_true")
    args = ap.parse_args()

    results: list[RepoResult] = []

    cosmic = _prepare("cosmic", "cosmicpython/code", shallow=False)
    if cosmic:
        for branch, label in COSMIC_BRANCHES:
            if _git(["git", "checkout", "--quiet", branch], cwd=cosmic):
                print(f"  skip {branch} (checkout failed)", file=sys.stderr)
                continue
            r = analyze(cosmic, "A_present", branch, label)
            if r:
                results.append(r)
                _print_row("A", branch, r)

    for name, repo, label in CONTROL_REPOS:
        target = _prepare(name, repo)
        if not target:
            continue
        r = analyze(target, "B_absent", name, label)
        if r:
            results.append(r)
            _print_row("B", name, r)

    manifest_total = 0
    if not args.skip_corpus_c:
        manifest = load_manifest()
        manifest_total = len(manifest)
        for proj in manifest:
            target = clone_or_update(proj)
            if not target:
                continue
            src = target / proj.get("src_dir", "")
            r = analyze(src if src.exists() else target, "C_na", proj["name"], proj.get("why", ""))
            if r:
                results.append(r)
                _print_row("C", proj["name"], r)

    summarize(results, manifest_total)
    if args.json:
        args.json.write_text(json.dumps([r.model_dump() for r in results], indent=2))
    return 0


DETECTORS = [
    ("D_purity", "purity_domain", "purity_violations", "purity_tp"),
    ("D_naming", "naming_domain", "naming_violations", "naming_tp"),
    ("D_position", "position_domain", "position_violations", "position_tp"),
    ("D_position_relaxed", "relaxed_domain", "relaxed_violations", "relaxed_tp"),
]


def summarize(results: list[RepoResult], manifest_total: int = 0) -> None:
    print("\n" + "=" * 78)
    for detector, dom, vio, _tp in DETECTORS:
        print(f"\n{detector}")
        for corpus in ("A_present", "B_absent", "C_na"):
            rows = [r for r in results if r.corpus == corpus]
            if not rows:
                continue
            flagged = [r for r in rows if getattr(r, vio) > 0]
            empty_domain = [r for r in rows if getattr(r, dom) == 0]
            print(
                f"  {corpus:10s} n={len(rows):2d}  "
                f"flagged={len(flagged):2d}/{len(rows):2d}  "
                f"empty-domain(vacuous)={len(empty_domain):2d}/{len(rows):2d}"
            )

    # Domain identification: does the detector find the domain at all? This is
    # the number the repo-level verdict hides.
    labeled = [r for r in results if r.truth_size]
    if labeled:
        print("\ndomain-identification precision (TP / inferred), recall (TP / truth)")
        header = f"  {'repo':34s}" + "".join(f"{d:>22s}" for d, _, _, _ in DETECTORS)
        print(header)
        for r in labeled:
            cells = ""
            for _d, dom, _vio, tp in DETECTORS:
                inferred, hit = getattr(r, dom), getattr(r, tp)
                prec = f"{hit / inferred:.2f}" if inferred else " n/a"
                rec = f"{hit / r.truth_size:.2f}"
                cells += f"{prec:>12s}/{rec:<9s}"
            print(f"  {r.name:34s}{cells}")

    c_rows = [r for r in results if r.corpus == "C_na"]
    if manifest_total and c_rows:
        print(
            f"\ncorpus C coverage: {len(c_rows)}/{manifest_total} manifest projects analyzed "
            f"({manifest_total - len(c_rows)} unavailable or unparseable)"
        )
    flagged_c = [r for r in c_rows if r.position_violations]
    if flagged_c:
        print("corpus C D_position violations (after self-stack subtraction):")
        for r in flagged_c:
            flagged = list(r.position_flagged)[:4]
            print(f"  {r.name:20s} {r.position_violations}/{r.position_domain}  {flagged}")


if __name__ == "__main__":
    raise SystemExit(main())
