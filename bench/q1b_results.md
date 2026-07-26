# Q1b arm-B pilot: how often does an unaided agent break structure?

**Result: 0 of 25. Not one run introduced an import cycle or violated a declared
layer rule.** Run 2026-07-25/26, `claude-sonnet-5`, arm B only (no archy in the
loop), via `bench/q1b_run.py`. Raw rows: `bench/q1b_results.jsonl`.

This is the number the whole Q1b harness (#349, #351, #352, #353, #354, #355)
was built to obtain, and it is a null.

## The numbers

| | |
| --- | --- |
| runs | 25 of the top-25 structurally-riskiest SWE-bench tasks |
| measurable | 25 (0 dropped) |
| **p_B (pooled)** | **0/25 = 0%** |
| p_B (edited runs only) | 0/24 = 0% (1 run changed nothing) |
| cycle regressions | 0 |
| declared-layer violations introduced | 0 |
| files changed | 332 total, median 12 per run, range 0-35 |
| agent turns | median 64, range 12-261 |
| agent wall time | 3.2h total, median 6.5 min per run |

Per repo, because ruleset strength differs (see `q1b_layers/README.md`):
django 0/9, sympy 0/8, xarray 0/3, scikit-learn 0/2, matplotlib 0/2,
requests 0/1.

**The agents did substantial work.** 332 files changed across 25 runs, a median
of 64 turns. This is not a null produced by agents declining the tasks; exactly
one run made no edit at all, and it is excluded from the second rate above.

## The pre-registered reading

From `bench/q1b_tasks.py`, committed in #351 **before** any agent ran:

> **p_B >= 25%** -> powered at ~80-130 pairs, proceed to arm A.
> **p_B <= 10%** -> the corpus is wrong, not archy.

0/25 lands in the second branch. By the rule of three, the 95% upper bound on
p_B is **12%**, so the honest statement is "p_B is below roughly 12%", not "the
event never happens". Either way the paired A/B is unrunnable here: at p_B = 5%
it would need on the order of a thousand pairs.

**Why the corpus, and not the tool, is the thing indicted.** This was measured,
not assumed, and measured first: 430 of SWE-bench Verified's 500 gold patches
touch exactly **one** `.py` file (#351). A corpus of single-file bug fixes
cannot exhibit multi-file structural damage. The tasks used here are the *most*
structurally risky 25 of 211 that clear a >=3-file/>=2-directory filter, and
even those are localized fixes.

**This card can only be played once.** "The corpus was wrong" is a legitimate
conclusion exactly once, because it was pre-registered. The successor corpus
(real multi-file feature and refactor commits) must be pre-registered the same
way, and **if it also returns ~0, that is evidence against the thesis itself**,
not another corpus problem. Without that commitment this becomes a search for a
dataset where archy wins.

## What this does NOT show

- **It does not measure archy's detection ability.** This pilot measures how
  often the event *occurs*, not whether archy *catches it* when it does. Those
  are different quantities. Detection-recall against a developer-declared oracle
  is #347, and needs no agent time.
- **It does not show agent edits are structurally harmless in general.** It
  shows that on localized bug-fix tasks, in six mature repositories, with the
  rules authored in `bench/q1b_layers/`, nothing fired.
- **It is not evidence archy is unnecessary**, and must not be reported as such.

## Sensitivity: could the measure have missed something?

A null is worthless if the instrument was dead, so both halves were shown to
fire on the same code path the runner uses, on `requests` @ `d64b9ad4`:

```
clean tree : cycle=False layers={}
layer break: cycle=False layers={'compat->models': 1}   # compat.py imports models
cycle break: cycle=True  new_cyclic=('requests.help','requests.status_codes')
```

`bench/q1b_layers_check.py --canary` makes the same guarantee for all six
configs, and `--base-commits` confirms every rule is silent on all 25 base
commits, so any violation observed would have to be one the agent introduced.

Three real limits on sensitivity remain, and they are limits of *this* measure,
not evidence of absence:

1. **The rulesets are coarse.** 7 to 22 rules per repo over top-level layers. An
   agent could make a structurally poor change that no authored rule describes.
2. **Tests are excluded from the scan** (`exclude: ["tests"]`, uniform across
   all six configs), so a violation introduced inside a `tests/` directory
   cannot fire.
3. **The cycle definition is deliberately strict** (Q1a's verbatim: cycle count
   up AND a previously-acyclic module now in an SCC), chosen for zero false
   positives against the human baseline it must be comparable to.

## Score regression: 12 of 25, and why it is reported as noise

`score_regression` fired on 12 of 25 runs. **Every single delta is inside Q1a's
0.005 noise floor**: the largest drop was 0.004, 23 of 25 were under 0.001, and
the median was exactly 0.0.

This is the trap the design anticipated. "archy detects degradation in 48% of
agent edits" is a headline available from this data and it would be theater:
Q1a Finding 3 already established that 98% of human score drops are under 0.005
and that the composite score moved *up* on the worst structural event in that
corpus. Score is recorded, never gates, and 12/25 is a fact about float noise.

## Harness defects found by running it

Five, all in plumbing, none in the measurement. Recorded because the pattern is
the reusable part: the measurement was built and validated before any spend, so
every bug the live run found was cheap.

| defect | how it presented | cost |
| --- | --- | --- |
| `git checkout` before `reset --hard` | agents `git add` their work, so the next task's checkout hit "local changes would be overwritten" | 11 runs, first attempt |
| plain `git diff` misses staged edits | runs that edited the tree reported `files_changed=0` and read as "agent changed nothing" | wrong metadata on 5 runs |
| agent inherited `VIRTUAL_ENV` from `uv run` | a scikit-learn task's `pip` uninstalled `scipy` from **archy's own venv**, breaking an unrelated archy test | 1 broken test, and an agent able to modify the environment the measurement runs in |
| failure rows logged stderr only | `agent exited 1:` with nothing after the colon; the CLI writes its JSON to stdout | 1 undiagnosable failure |
| rate-limit matcher scanned agent output | a *successful* run whose agent text mentioned "429" triggered a 5m, then 10m backoff, and would have halted the pilot after 8 waits | ~1h wall clock |

A sixth non-defect worth recording: a 4-hour "hang" was the laptop entering
maintenance sleep on battery. Run long pilots under `caffeinate -i -m -w <pid>`.

## What happens next

1. **#347 detection recall** against a developer-declared oracle. It answers the
   question this pilot cannot (does archy *catch* the event), costs no agent
   time, and is now the higher-value experiment.
2. **A pre-registered successor corpus** of multi-file feature/refactor commits,
   with the falsification commitment stated above written into the ticket before
   it runs.
3. **No arm A on this corpus.** Running the archy arm here would compare 0
   against 0 and produce a meaningless "no difference".
