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
                           the discriminant-validity corpus: a detector that
                           calls these "compliant" is emitting vacuous signal,
                           which is the OECD discriminant check AXIS_REVIEW.md
                           requires and the failure mode #142 warns about.

Four candidate detectors, spanning the inference-vs-declaration axis:

  D_purity   -- domain := internal modules importing nothing non-stdlib.
                Violation := such a module importing an infra package.
  D_naming   -- domain := modules whose qualname carries a domain-ish token
                (model/domain/entities/...). Violation := direct infra import.
  D_position -- domain := graph sinks (internal fan-in > 0, internal fan-out
                == 0), the "everything depends on it, it depends on nothing"
                shape the hexagonal ideal describes. Violation := infra import.
  D_declared -- the contracts baseline: the user names the domain package, and
                the check is a Forbidden contract. This is what archy ships
                TODAY (`archy_check(contracts=True)` with
                `include_external_packages = True`); it is here to measure the
                delta any inferred detector would have to beat.

Note that archy's *graph* cannot answer this question on its own: external
imports are collapsed by `_external_target` and dropped by `assemble_graph`
(the graph is internal-only). Every detector below therefore reads
`parse_project`'s ParseResult imports, one layer below the graph.

Usage:
    uv run --extra contracts python bench/pattern_detection.py
    uv run --extra contracts python bench/pattern_detection.py --json out.json
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
    infra_using_modules: int


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
    # expected and would swamp every detector.
    real = {
        q
        for q in ext
        if not any(p.startswith("test") or p == "tests" or p == "conftest" for p in q.split("."))
    }
    ext = {q: v for q, v in ext.items() if q in real}

    graph = assemble_graph(root, modules, parse_results)
    internal_graph = nx.DiGraph()
    internal_graph.add_nodes_from(real)
    for src, dst in graph.edges():
        if src in real and dst in real:
            internal_graph.add_edge(src, dst)

    # D_purity: domain := imports nothing external at all.
    purity_domain = {q for q in real if not ext[q]}
    purity_violations = {q for q in purity_domain if _infra_of(ext[q])}

    # D_naming: domain := carries a domain-ish name token.
    naming_domain = {q for q in real if _has_domain_token(q)}
    naming_violations = {q for q in naming_domain if _infra_of(ext[q])}

    # D_position: domain := internal sink (imported by peers, imports no peer).
    position_domain = {
        q for q in real if internal_graph.in_degree(q) > 0 and internal_graph.out_degree(q) == 0
    }
    position_violations = {q for q in position_domain if _infra_of(ext[q])}

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
        infra_using_modules=len([q for q in real if _infra_of(ext[q])]),
    )


def _git(args: list[str], cwd: Path | None = None) -> int:
    return subprocess.run(args, cwd=cwd, capture_output=True).returncode


def prepare_cosmic() -> Path | None:
    target = WORKDIR / "cosmic"
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        if _git(
            ["git", "clone", "--quiet", "https://github.com/cosmicpython/code.git", str(target)]
        ):
            return None
    return target


def prepare_control(name: str, repo: str) -> Path | None:
    target = WORKDIR / name
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        if _git(
            [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                f"https://github.com/{repo}.git",
                str(target),
            ]
        ):
            return None
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--skip-corpus-c", action="store_true")
    args = ap.parse_args()

    results: list[RepoResult] = []

    cosmic = prepare_cosmic()
    if cosmic:
        for branch, label in COSMIC_BRANCHES:
            if _git(["git", "checkout", "--quiet", branch], cwd=cosmic):
                print(f"  skip {branch} (checkout failed)", file=sys.stderr)
                continue
            r = analyze(cosmic, "A_present", branch, label)
            if r:
                results.append(r)
                print(
                    f"A {branch:42s} n={r.n_modules:3d} "
                    f"purity={r.purity_violations}/{r.purity_domain} "
                    f"naming={r.naming_violations}/{r.naming_domain} "
                    f"position={r.position_violations}/{r.position_domain}"
                )

    for name, repo, label in CONTROL_REPOS:
        target = prepare_control(name, repo)
        if not target:
            continue
        r = analyze(target, "B_absent", name, label)
        if r:
            results.append(r)
            print(
                f"B {name:42s} n={r.n_modules:3d} "
                f"purity={r.purity_violations}/{r.purity_domain} "
                f"naming={r.naming_violations}/{r.naming_domain} "
                f"position={r.position_violations}/{r.position_domain}"
            )

    if not args.skip_corpus_c:
        for proj in load_manifest():
            target = clone_or_update(proj)
            if not target:
                continue
            src = target / proj.get("src_dir", "")
            r = analyze(src if src.exists() else target, "C_na", proj["name"], proj.get("why", ""))
            if r:
                results.append(r)
                print(
                    f"C {proj['name']:42s} n={r.n_modules:3d} "
                    f"purity={r.purity_violations}/{r.purity_domain} "
                    f"naming={r.naming_violations}/{r.naming_domain} "
                    f"position={r.position_violations}/{r.position_domain}"
                )

    summarize(results)
    if args.json:
        args.json.write_text(json.dumps([r.model_dump() for r in results], indent=2))
    return 0


def summarize(results: list[RepoResult]) -> None:
    print("\n" + "=" * 78)
    for detector, dom, vio in [
        ("D_purity", "purity_domain", "purity_violations"),
        ("D_naming", "naming_domain", "naming_violations"),
        ("D_position", "position_domain", "position_violations"),
    ]:
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


if __name__ == "__main__":
    raise SystemExit(main())
