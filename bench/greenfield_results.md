# Does a structural checker in the loop help an agent build to a specified architecture? (#369)

**Result: yes, by +12.0 pp, and the effect is capped by the ceiling rather than
by archy.** An unaided agent already complied 88% of the time, so the most any
treatment could have scored was +12. Arm B took it to 100% at no behavioral
cost.

Run 2026-07-26/27, `claude-sonnet-5`, FastAPI, the paper's L1-Arch condition,
25 runs per arm, 7.0 hours of agent time. Raw rows:
`bench/greenfield_results.jsonl`. Harness: `bench/greenfield_run.py`.
Thresholds, fixed in advance: `bench/greenfield_prereg.py`.

This is the first positive result in this line of work. It is also smaller and
narrower than the tool's original claim, and it does not restore it.

## The numbers

| | arm A (static prompt) | arm B (archy in the loop) |
| --- | --- | --- |
| runs | 25 | 25 |
| structurally evaluable | 25 | 25 |
| **compliant** | **22 (88.0%)** | **25 (100.0%)** |
| 95% Wilson | 70.0 - 95.8% | 86.7 - 100% |
| behavioral pass rate, compliant runs | 79.2% | 78.9% |
| **composite A%** (paper's headline) | **69.7%** | **78.9%** |
| agent wall time, median | 8.7 min | 7.4 min |
| agent turns, median | 67 | 65 |
| checker invocations | 0 on every run | 7-13, median 10 |

**delta = +12.0 pp, 95% CI -3.5 to +30.0.**

## Against the thresholds, written before the run

| pre-registered condition | observed | verdict |
| --- | --- | --- |
| `delta >= +25 pp` and CI excludes 0 | +12.0 pp, CI includes 0 | not met |
| `delta < +10 pp` | +12.0 pp | not met |
| anything between, or CI includes 0 | **both** | **EXPAND** |
| behavioral guardrail (arm B may not fall >5 pp) | -0.3 pp | **held** |

## The flaw in the design, stated plainly

**The win condition was arithmetically unreachable.** `WIN_DELTA_PP = 25` was
set from a plausible arm-A rate near 40%, inferred from the paper. The actual
arm-A rate was 88%, which caps the maximum possible delta at +12. Arm B could
have been perfect, and it was, and it still could not have scored a WIN.

The threshold is not being reinterpreted; EXPAND is the honest reading and it
stands. But the reason it reads EXPAND is a fact about the design, not about
archy, and a write-up that reported "EXPAND, inconclusive" without saying so
would be misleading.

**The process lesson, which generalizes past this study:** the repo's rule is
"build and validate the measurement before spending agent time." That rule
applies to the *design parameters* too, not only the harness. A 5-run arm-A
pilot would have cost about an hour and would have shown 88% before the
thresholds were frozen, at which point the correct move was a harder condition,
not a bigger N.

## What the failures actually were, which is sharper than the headline

All three non-compliant arm-A runs failed the same way, and none failed on
layer presence:

| run | layers present | violation |
| --- | --- | --- |
| conduit-06:A | 4 of 4 | `conduit.models.article -> conduit.repository.*` (4 edges) |
| conduit-11:A | 4 of 4 | `app.models.article -> app.repositories.*` (4 edges) |
| conduit-15:A | 4 of 4 | `app.models.* -> app.repository.*` (4 edges) |

**Every agent got the directory layout right and the dependency direction
wrong**, in the same place: entities reaching down into data access. Prose in a
prompt did not prevent it; a directional `forbid` rule caught all of it. That is
the aspiration-versus-consequence mechanism the Constraint Decay discussion
proposed, showing up as data rather than as an argument.

## What survives the instrument overlap

The pre-registration discloses that arm B optimizes against the same checker
that scores it, so arm B approaching 100% is partly tautological. Three things
survive that overlap, and they are the result:

1. **Residual non-compliance in arm B is 0 of 25.** Every gated run converged,
   in a median of 10 checker cycles. Convergence was not guaranteed; an agent
   that thrashed or gave up would have shown here.
2. **The behavioral guardrail held at -0.3 pp** against a -5 pp bar. The
   structural gain was not bought by breaking the server. The suite is the
   RealWorld project's, which the checker does not touch.
3. **Composite A% rose 69.7% to 78.9%**, and that requires both halves.

## What this does NOT show

- **It is not about existing codebases.** #356 asked whether an agent *damages*
  an architecture already present and got 0 of 25. This asks whether an agent
  *constructs* one correctly. Neither study tests the other's regime, and this
  result does not revive the retracted headline claim.
- **It is one model, one framework, one condition.** `claude-sonnet-5`, FastAPI,
  L1-Arch, which is the paper's *mildest* architectural condition. The paper's
  30 pp decay is about constraints accumulating to L3.
- **Absolute rates are not comparable to the paper's table.** The behavioral
  oracle is the RealWorld Hurl suite, substituted for an unpublished Postman
  collection, and the task prompt's non-constraint text is this harness's own.
- **12% is the size of the problem here.** A tool that closes a 12% failure rate
  is not the same claim as a tool that closes a 30 pp one.

## Headroom is the pattern, across four studies now

| study | question | headroom found |
| --- | --- | --- |
| #282 | does cleanliness cut agent footprint? | null |
| #289 | does a brief cut exploratory reads? | null |
| #356 | do agents damage existing architecture? | 0 of 25 |
| **#369** | do agents build a specified architecture? | **3 of 25 fail** |

Four attempts, and the binding constraint every time was how rarely the problem
occurs, not whether archy detects it. The Constraint Decay paper's own reception
predicted this: frontier models were not fully tested there for cost reasons, so
its absolute numbers are directional. On a 2026 model the mildest architectural
constraint is satisfied 88% of the time unaided.

## The one place this result is load-bearing

#364 measured that **standing violations do not get fixed**: no violation in the
sampled corpus was ever resolved, and 2 of 14 repositories sat on broken
contracts indefinitely. A directional violation introduced at scaffolding time
is therefore likely to persist for the life of the project.

That is the honest case for catching it at generation: not that it happens
often, but that it is cheap to prevent at birth and evidently never repaired
afterwards. Whether a 12% birth-defect rate justifies the tool is a product
judgment, not a measurement, and this document does not make it.

## Harness defects found by running it

Four, all of which would have corrupted the result, and three found before any
scoring:

| defect | what it would have done |
| --- | --- |
| `--network host` unreachable on Docker Desktop macOS | suite ran and reported 13/0, so an up-and-answering server scored an evaluable `pass_rate=0.0`. **Both arms zeroed.** |
| layer patterns dead against a package-nested tree | a real backend with `api/`, `services/`, `infrastructure/` read as **0 of 4 layers, non-compliant**. The agents nested under `app/` in almost every run. |
| behavioral metric was whole-file | a near-conforming backend and a stub that 501s everything **both scored 0.000**. The guardrail could never have fired. |
| `uv venv` over an agent-created venv | 4 of the first 10 live runs scored a harness-caused behavioral zero, **3 in arm A against 1 in arm B**, biasing toward the treatment. |

The last one was caught by a manual audit, which is why an automated integrity
gate now runs between chunks and stops the batch on harness-shaped failures or
on any arm-B run that never invoked the checker. It passed 5 times across the
remaining 40 runs.

A fifth, in the shared `Ledger`: appending after a torn final row concatenated
the two and destroyed both, so one hard kill cost two units. `bench/q1b_run.py`
carried the same defect.

## Reproducing

```
uv run python bench/greenfield_eval.py --fetch-suite
uv run python bench/greenfield_run.py --dry-run
uv run python bench/greenfield_run.py --limit 5     # a 10-run chunk
uv run python bench/greenfield_run.py --report      # refuses before N=25/arm
```
