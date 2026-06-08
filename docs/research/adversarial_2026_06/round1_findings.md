# Round 1 adversarial findings (144 surviving)


## bench / call_weighted-bench


### [blocker] Orthogonality claim compares weighted Q against wrong baseline values  (confirmed)
- **loc**: bench/call_weighted_modularity_results.md:91, bench/call_weighted_modularity.py:198-199
- **claim**: The benchmark claims weighted Q is 'substantially more orthogonal' to other axes than unweighted Q, citing correlations from RESEARCH_METRICS.md sec 16 as the baseline. However, sec 16 measures calls_per_edge orthogonality, not unweighted modularity orthogonality.
- **evidence**: RESEARCH_METRICS.md section 16 (line 1065-1071) shows correlations for 'calls_per_edge' signal: modularity r=+0.148, acyclicity r=+0.208, depth r=-0.062, equality r=+0.212. The bench cites +0.423, -0.576, -0.344 as baseline but these numbers are not the actual unweighted modularity correlations on the 27-project bench. Computing actual unweighted Q correlations yields: acyclicity=-0.192, depth=-0.206, equality=+0.022, which are completely different.
- **impact**: The central justification for shipping call-weighted Q as an independent diagnostic is invalidated. The claimed orthogonality advantage does not exist when comparing against the actual baseline. Users cannot rely on reported improvement in independence from other axes.
- **fix**: Recompute unweighted Q correlations against the other four axes on the actual 27-project bench. Update bench results with correct baseline values. Re-evaluate whether the orthogonality argument still holds with accurate numbers.
- **notes**: 1. **Citation error**: The call_weighted_modularity_results.md lines 83-89 cite baseline values with attribution "from sec 16" of RESEARCH_METRICS.md, but section 16 actually documents `calls_per_edge` correlations, not unweighted modularity correlations. The baseline values don't exist in that section.

2. **Wrong baseline values**: The actual unweighted modularity correlations from bench/results.md (the official 27-project benchmark) are:
   - acyclicity: +0.383 (cited as +0.423)
   - depth: -0.617 (cited as -0.576)  
   - equality: -0.389 (cited as -0.344)

3. **Impact on orthogonality claims**: The comparison in call_weighted_modularity_results.md line 91 stating "All four cross-axis correlations drop in absolute value" depends on these baseline values. With correct baselines, the magnitude claims require re-evaluation:
   - acyclicity: 0.217 vs 0.383 (43.3% reduction - still true)
   - depth: -0.397 vs -0.617 (35.7% reduction - still true)
   - equality: 0.044 vs -0.389 (signs differ; comparison invalid)

4. **Root cause**: The call_weighted_modularity.py script (lines 197-199) doesn't compute the baseline correlations; it just directs users to check RESEARCH_METRICS.md section 16, which contains the wrong signal entirely.

**Ticket scope**: (1) Fix the citation/attribution error to point to the correct source (bench/results.md) or compute baselines inline; (2) Correct the baseline values to match the actual 27-project bench; (3) Re-evaluate whether the "substantially more orthogonal" claim still holds with accurate numbers; (4) Update call_weighted_modularity_results.md and CALL_WEIGHTED_Q_EMPIRICS.md with correct values.

### [minor] Rank shifts lack validation against ground truth  (confirmed)
- **loc**: bench/call_weighted_modularity_results.md:41-72
- **claim**: Projects shift up to 24 rank positions under call-weighting (e.g., msgspec rank 3→27) with narrative explanations but no validation that reordered ranks are more truthful than original ranks.
- **evidence**: msgspec moves from rank 3 to 27. sqlalchemy rank 9→26, pygments rank 6→25. The bench offers explanations ('calls cross boundaries') but no ground truth validation. No evidence that users/experts agree these reorderings are correct. For small projects like msgspec (131 modules), any weighting choice swings the partition wildly.
- **impact**: Users may act on reordered rankings without realizing they're unvalidated. A project reordered from rank 3 to rank 27 could trigger harmful refactoring without architectural justification.
- **fix**: Add ground-truth validation: survey maintainers on which projects have better modularity. Check if weighted reordering correlates with expert judgment. Without this, rank shifts are noise.
- **notes**: The finding correctly identifies that narrative explanations lack empirical ground-truth validation. However, severity should be downgraded from "major" because: (1) the call-weighted modularity is a diagnostic output only, not a score axis replacement; (2) the headline `archy score` is unaffected by the reordering; (3) users cannot passively trigger harmful refactoring based on score alone since the reorderings are not in the score; (4) the authors are transparent about the validation gap and explicitly deferred the 10-expert study as future work. The ticket should capture: (a) the narratives remain post-hoc structural interpretations without expert validation; (b) the diagnostic design constrains the surface where these interpretations reach users; (c) if the diagnostic grows beyond parallel comparison (e.g., if future versions attempt to use weighted Q as the headline modularity), the validation gap becomes a blocker and would need the 10-expert study before promotion.

### [minor] Benchmark ignores call-resolution accuracy and coverage issues  (confirmed)
- **loc**: bench/call_weighted_modularity.py, RESEARCH_METRICS.md:1141-1149
- **claim**: The benchmark weights edges by call_count without analyzing call-resolution accuracy. Documented limitations include 'alias-only resolution,' 'no class/attribute tracking,' and 'decorator errors.' These could systematically skew Q values, but the benchmark never reports coverage or sensitivity.
- **evidence**: RESEARCH_METRICS.md lines 1141-1149 list false-negative and false-positive sources in call resolution. The benchmark applies these noisy counts directly as weights without sensitivity analysis. The bench never reports per-project call-resolution coverage (resolved / total attempted).
- **impact**: Q_weighted values may be artifacts of call-resolution noise rather than true signals of architecture. Architectural decisions based on Q_weighted could amplify these artifacts.
- **fix**: Report per-project call-resolution coverage. Rerun analysis on high-coverage projects only. Perform sensitivity analysis: how would Q_weighted shift if 10-20% of call weights were randomly flipped?
- **notes**: The benchmark has a real but bounded limitation: it lacks sensitivity analysis and call-resolution coverage reporting. The finding correctly identifies this gap. However, the ticket should clarify that: (1) This analysis was not performed because the shipped feature is a parallel diagnostic (gap comparison), not a replacement metric, so the call-count noise affects both values together rather than creating independent artifacts; (2) The intended use case requires reading both unweighted Q and weighted Q together, making the comparative view the actual signal; (3) Future improvements should include: (a) optional reporting of call-resolution coverage rates per project (for transparency), (b) sensitivity analysis showing how the gap changes under perturbation of call weights, (c) documentation advising against overinterpreting either absolute value without the other. The current implementation does not "amplify artifacts" into the core modularity axis (which remains unchanged) but rather provides a side-by-side comparison. Updating the documentation to emphasize this comparative nature would reduce the risk further.

## bench / corpus


### [major] Self-referential validation: archy measures itself as proof of its own metric  (confirmed)
- **loc**: bench/projects.yaml:187-191, bench/run.py:217-220, cited in SCORING.md:413-509
- **claim**: archy is included in the benchmark corpus (v0.22.0 pinned) as a data point. The correlation evidence for axis independence (results.md §Pairwise correlations) includes archy's row, making the validation tautological: archy's score is computed from the same formula being defended by the benchmark.
- **evidence**: bench/projects.yaml line 187-191 pins archy itself with rationale 'dogfooding'. When a new axis (e.g., complexity promoted in v0.20) is added, archy's score changes, and that changed row appears in the correlation table cited as evidence for the axis's orthogonality. The benchmark compares archy (tool) measuring archy (subject) against itself as the validation oracle—this conflates the metric design with its validation.
- **impact**: The claim in SCORING.md (line 428-434) that 'all ten pairs are below |r| = 0.7, the OECD-conventional threshold for treating sub-indicators as redundant' is undermined by including a self-referential data point. When the geometric-mean formula is updated (as happened in v0.20 and v0.23), archy's own row shifts, changing the correlation matrix. This makes it impossible to distinguish whether correlations reflect true axis independence or the design choices baked into the formula itself.
- **fix**: Either (1) exclude archy from the corpus and benchmark against a held-out external codebase of comparable size/domain, or (2) explicitly document that archy is a dogfooding data point and compute separate correlation statistics with and without it to show robustness. The prompt warns against 'comparing a SUBSET of output fields hides bugs'—this is a SUBSET of data points hiding the self-reference.
- **notes**: The finding is CONFIRMED. Ticket should capture:

1. **The core issue**: Archy appears in the benchmark as both the analyzing tool and a data point, creating a self-referential validation loop. When the scoring formula changes (as happened in v0.20 adding complexity, v0.23 recalibrating complexity), archy's own row shifts, which propagates into the correlation matrix cited as evidence for formula correctness.

2. **Concrete evidence of impact**: 
   - Excluding archy from correlation calculations changes several results measurably (acyclicity↔depth shifts from -0.581 to -0.653; depth↔equality shifts from +0.358 to +0.475)
   - Archy's unusual profile (acyclicity=1.0, depth=0.667 high, equality=0.273 lowest) means it's an outlier that can influence correlation structure

3. **What the code admits**: bench/projects.yaml's own comments state "every merged commit was silently shifting archy's row in the score table and propagating into the correlation matrix, since archy is one of the data points." This is not a hidden issue—it's acknowledged in a code comment.

4. **The mitigation is incomplete**: Pinning to a release tag (v0.22.0) stops the silent drift but doesn't address the methodological confound. The correlation table in SCORING.md §413-426 still uses archy as a data point in the very table used to justify the formula.

5. **Suggested actions** (from the finding):
   - Publish correlations computed two ways: with and without archy, to show robustness
   - Explicitly document in SCORING.md that archy is included as a dogfooding case and note the scope implications
   - Consider holding out archy from axis-independence validation (use the 27 external projects only)

6. **Not a blocker for v1.0**: The axis-independence claim (OECD |r| < 0.7 threshold) still holds even without archy (all pairs still <0.7, though some shift). But the validation evidence is weaker than currently presented.

Lines to update in docs: SCORING.md §413-434 (add footnote about archy's role); possibly bench/run.py to compute and output both correlation matrices; LEARNINGS.md to note this as a future design revisit.

### [major] Corpus selection bias: cherry-picked projects reinforce the metric design  (confirmed)
- **loc**: bench/projects.yaml:14-191 (entire corpus rationale), results.md (reported as independent validation)
- **claim**: The 28-project corpus was built by starting with 9 'popular' projects, then adding layers (size/domain, async, plugin host, tooling, distinctive shapes) with post-hoc narrative justifications. This is confirmatory bias, not representative sampling. No negative test cases (projects that score poorly, or are mid-refactor) exist to falsify weak metrics.
- **evidence**: Line 14: 'preserved from prior benchmarks' locks in the original 9 without re-evaluating fitness. Line 157-174: 'distinctive surface shapes' includes boto3 (hand-written facade), botocore (auto-generated), pygments (registry pattern), and setuptools (vendored). These are four unrelated patterns, not a coherent selection category. No justification is given for why msgspec (0.397, the lowest scorer) was added, or why projects scoring 0.3-0.4 range are absent. The corpus is all mature, stable projects at pinned SHAs—no projects in active refactoring cycles (archy's stated target use case).
- **impact**: The benchmark demonstrates that the metric 'works on these 28 projects,' not that it 'reliably measures architectural quality across all Python codebases.' When SCORING.md (line 542-550) interprets the score bands using these 28 projects as reference, it's reasoning from a biased sample. An agent on a refactoring-phase codebase (archy's target use case) gets no signal about whether the metric will predict refactor difficulty or just measure static shape.
- **fix**: Pre-specify selection criteria before adding projects (e.g., 'include projects scoring 0.3-0.7 to cover the full range,' 'include projects in active refactoring based on git history'). Add negative test cases: projects known to have architectural issues, projects mid-migration, projects that improved/declined over time. Document the corpus as 'a demonstration set' not 'validation evidence,' or produce a second held-out corpus with different selection criteria to test robustness.
- **notes**: Scope the ticket must capture:

1. **Selection bias confirmed**: The 9-library core was preserved without fitness re-evaluation. Projects were added to fill observed gaps (5c5f30b → a9d3474 → 7fdf19f → 680bb05), not against pre-specified criteria. Commit 7fdf19f's message reveals "download-popular but missing" as the selection driver, and the four added projects (boto3, botocore, pygments, setuptools) have no coherent architectural principle beyond that—they're unrelated patterns (facade, auto-generated SDK, registry, vendored tooling) grouped post-hoc.

2. **Corpus homogeneity on maturity**: All 28 projects are pinned to stable SHAs; none are in active refactoring. CASE_STUDIES.md explicitly describes them as "9 mature codebases" and "heavily-reviewed, production code." Score distribution shows msgspec (0.397) as the only sub-0.4 outlier; gap to next tier (pytorch 0.478) is large. No projects with known architectural issues included.

3. **Impact on generalization claim**: The benchmark validates metric consistency on mature, download-popular projects. However, archy's stated target use case (ROADMAP.md, FUTURE.md) includes agents on refactoring-phase codebases. The corpus does not cover that scenario, creating a validation gap for the tool's primary use case.

4. **Documentation positioning**: SCORING.md lines 500-509 cite systematic-mapping literature requiring empirical samples for threshold derivation, then derive bands from this corpus without caveat that the sample skews toward stable/mature projects or may not generalize to in-flight codebases.

5. **Positive counter-evidence present**: SCORING.md lines 468-486 document robustness testing: authors tested 16 alternative metrics/aggregators against the bench and consciously kept the status quo, with reasoning. Lines 571-587 acknowledge top scorers are "shape-driven, not size-driven." This shows the authors are not naive to their sample's composition; however, the caveats remain localized to the axis-independence section and do not propagate to the band interpretation.

Ticket should frame as: "Benchmark composition creates validation risk for refactoring-phase use case. All projects are mature/stable; no negative test cases or active-refactoring examples; no separate validation corpus with different selection criteria. Recommended: either label corpus as 'demonstration' not 'validation', or add explicit caveat to band table about applicability to refactoring codebases."

### [major] Incomplete call-graph data used as orthogonality evidence while hidden in separate table  (confirmed)
- **loc**: bench/run.py:316-325, results.md:54-100, SCORING.md:656-670
- **claim**: Call-edge extraction covers only 34-91% of import edges (median 58.6%). The benchmark computes correlations of calls_per_edge against score axes (results.md line 92-99) as evidence for orthogonality, but this correlation is computed on INCOMPLETE DATA. The main score table (results.md line 8-37) omits call_edge_count entirely, hiding the incomplete coverage from readers.
- **evidence**: results.md §Call-graph diagnostics (line 54-85) shows aiohttp has 107/312 edges with call data (34.3% coverage), flask 36/94 (38.3%), scrapy 354/858 (41.3%). Yet results.md §Call-density orthogonality (line 87-99) computes Pearson r = +0.148 between calls_per_edge and modularity without noting that calls_per_edge is biased low due to missing extraction. The main claim in SCORING.md line 660-661 ('The 28-project benchmark shows `calls_per_edge` is orthogonal to every existing axis (max `|r| = 0.229`)') assumes complete data.
- **impact**: If call-edge extraction has systematic bias (e.g., harder to extract in certain code patterns), the reported correlation with modularity/acyclicity is meaningless. A reader sees 'calls_per_edge is orthogonal (|r| = 0.148)' and concludes the metric is independent, without knowing half the data is missing. The incomplete coverage is not explained by any spec document and is discoverable only by comparing two separate sections of results.md.
- **fix**: In run.py line 316-325, add a gate: fail if any project's call_edge_count / edge_count < threshold (e.g., 75%). Document in SCORING.md why call coverage varies (static analysis limitation? design choice?). In results.md main table, add a 'call_coverage' column so the completeness risk is visible alongside the score. If orthogonality is claimed, cite it only for projects with >80% coverage, or separately from those with <50%.
- **notes**: Key issues to address:

1. **Fix SCORING.md citation**: Update line 661 from "max `|r| = 0.229`" to "max `|r| = 0.217`" to match current 28-project benchmark, or add a note explaining the 27-project vs 28-project discrepancy.

2. **Document call-coverage limitation at point of claim**: In results.md near line 87 ("Call-density orthogonality"), add a note that correlations are computed on projects with call-edge coverage ranging from 34% (aiohttp) to 91% (archy), and that extraction uses import-alias-only static analysis. This is critical context for interpreting orthogonality claims.

3. **Make coverage visible in main table**: Consider adding a call_coverage % column to the Score table (results.md lines 8-37) so readers can see at a glance which projects have <50% coverage, or at minimum add a column to the Call-graph diagnostics table showing (call_edges/edges)%.

4. **Add coverage-quality gate in bench/run.py**: Lines 316-325 should include a check: if any project's call_edge_count / edge_count falls below a threshold (e.g., 30% or 40%), either fail the benchmark or flag it explicitly in the output. This prevents silent data-quality degradation if the call-extraction implementation changes.

5. **Separate orthogonality claim by coverage band** (optional but recommended): If promoting call density in future, cite orthogonality only for projects with >75% coverage, or separately report correlations for high-coverage vs low-coverage subsets to show whether the bias is real.

The issue is NOT that incomplete coverage is inherently wrong (it's a documented trade-off for lower false positives), but that the incompleteness is hidden from readers who encounter the orthogonality claims without seeing the diagnostics section, creating a false impression of data completeness.

### [major] Benchmark has no falsification gate: reports results but validates nothing  (confirmed)
- **loc**: bench/run.py:179-423, entire benchmark flow
- **claim**: run.py computes correlations and reports them in results.md, but performs zero validation. It does not fail if any axis pair exceeds |r| = 0.7 (OECD redundancy threshold), does not warn if moderate coupling is detected, does not compare to prior runs, and does not gate archy releases on benchmark findings. The benchmark is descriptive (reports numbers) not prescriptive (rejects bad designs).
- **evidence**: run.py lines 305-313 compute Pearson correlations but store them only in output_lines for writing to markdown. No assertion, no exit code check, no warning. Line 262 sorts rows for output but doesn't validate anything. If the current benchmark showed modularity ↔ depth at r = 0.85 (OECD red flag), run.py would still exit 0 and write it to results.md. The benchmark has no gating power; it's purely observational.
- **impact**: Results.md is cited in SCORING.md (line 413) as 'empirical validation,' but it's not validation—it's a report. An axis change ships if someone edits score.py; the benchmark is run post-hoc to document the outcome. This inverts the relationship: the design should gate on the benchmark, not the other way around. A weak axis can hide in plain sight in results.md for months (e.g., if a new axis is added in v0.20 and later found to have |r| > 0.7 with another axis, the benchmark would show it but the code wouldn't fail).
- **fix**: Add validation gates to run.py: (1) fail if any axis pair |r| > 0.7; (2) warn if any pair |r| ∈ [0.5, 0.7] and require an explicit exemption in the code; (3) compare to prior runs and fail if correlations shifted significantly; (4) gate release tags on benchmark passing. Or explicitly reframe the benchmark as 'informational reporting only' and stop citing it as 'validation' in SCORING.md.
- **notes**: The claim is accurate. Recommended actions from the finding are sound: (1) Add validation gates to run.py that fail if any axis pair |r| > 0.7, with optional exemptions for [0.5, 0.7] pairs that require explicit code-side annotation. (2) Add comparison to prior runs and fail on significant shifts. (3) Integrate benchmark passing as a release gate in CI (add `bench/run.py` invocation to .github/workflows/ci.yml or a separate gated workflow). (4) Alternatively, if benchmark is intentionally informational-only, explicitly reframe SCORING.md to remove the "validation evidence" framing and document the benchmark as "descriptive reporting post hoc, not prescriptive design gate." Current state leaves axis evolution unguarded. The moderate coupling of modularity↔depth (-0.617) and acyclicity↔depth (-0.581) are already noted in SCORING.md as acknowledged design compromises with documented empirics (SCORE_SHAPE_REDESIGN_EMPIRICS.md), but a NEW axis or formula change that worsened these correlations would ship silently. Recommend adding validation gates with exemptions for the two known moderate pairs so future changes are transparent.

### [minor] Corpus misaligned with archy's stated use case: all projects stable, none in active refactoring  (uncertain)
- **loc**: bench/projects.yaml, docs/MCP_DIRECTORIES.md, docs/LEARNINGS.md, docs/FUTURE.md
- **claim**: archy is designed for 'agent-edited repos' in rapid iteration cycles (MCP_DIRECTORIES.md), but the 28-project corpus is all mature, stable public libraries at pinned SHAs. No projects are in active refactoring, migrating architecture, or trending scores over time. The benchmark measures static shape on frozen codebases, not refactoring difficulty or agent impact on evolving repos.
- **evidence**: MCP_DIRECTORIES.md: 'designed to run in CI, in pre-commit... so coding agents can read their architectural impact before committing.' LEARNINGS.md documents the feature set for agent loops (archy_diff, archy_impact, archy_simulate). Yet the corpus has zero projects with git history enabled (most are ancient stable codebases). Pygments, boto3, numpy are not refactoring targets—they're architecturally settled. The governingdocs backend (mentioned in LEARNINGS.md line 52 as 665 modules) is NOT pinned in the corpus. No project in results.md has a 'before/after' refactoring snapshot.
- **impact**: The benchmark validates that archy's metrics are well-defined on static graphs, not that they predict refactoring difficulty. An agent on a codebase with high cyclomatic complexity or poor modularity gets no signal from the corpus about whether breaking that cycle will be easy (10 edges) or hard (thousands). The score bands in SCORING.md (line 542-550) reference the corpus as 'context' but the corpus has no data about refactoring trajectories, which is the agent's actual use case.
- **fix**: Build a second corpus ('refactoring corpus') with projects that have git history enabled, track score trends over time (via archy's own .archy/history.jsonl), and include projects mid-refactoring with documented architectural decisions. Or add a warning to SCORING.md: 'The score bands below are derived from static snapshot benchmarks on stable projects; they may not predict refactoring difficulty in active codebases.'
- **notes**: 1. **Confirm or deny the core factual claim**: The corpus is NOT designed to validate refactoring-prediction accuracy. It is designed to establish score calibration and metric orthogonality. This is correctly documented in SCORING.md but could be more explicit.

2. **If treatment is needed, it's documentation, not code**:
   - Add one sentence to the "Interpreting a score" section: "The bands below are calibrated against static snapshots of 28 diverse public libraries at pinned SHAs. They establish thresholds for structural shape; they do not predict refactoring difficulty or trajectory. For per-project trend tracking, use `archy score --record` and `archy trend`."
   - Optionally, add a "Limitations" subsection to SCORING.md that clarifies: "These score bands are orthogonality-validated on diverse mature codebases. They are well-suited for: (1) identifying architecturally weak axes on a snapshot, (2) detecting drift over a project's own history. They are NOT validated for: predicting refactoring cost, estimating time-to-fix, ranking projects by "improve-ability."

3. **Governingdocs backend**: It's currently used in research benchmarks (bench/score_redesign.py) but not in the pinned corpus. FUTURE.md line 49 already documents "refactoring corpus" as a potential follow-up. No action required unless there's explicit intent to add it.

4. **The "agent loop" framing is orthogonal**: archy's agent-feedback framing is about catching *per-commit* regressions (snapshot/diff cycles), not about validating absolute thresholds. The corpus confusion doesn't undermine that use case.

### [minor] Size distribution is bimodal with missing middle: no 200-400 module backends  (confirmed)
- **loc**: bench/projects.yaml corpus (10 tiny <50, 5 small 50-100, 5 medium 100-300, 7 large 300-1000, 1 huge 2252)
- **claim**: The corpus has 10 projects under 50 modules and 7 projects over 300, but only 5 in the 100-300 range. This bimodal distribution may bias correlation tests. Removing the outlier (pytorch at 2252 modules), which is also the highest modularity (0.680) and lowest depth (0.286), may significantly shift the observed correlations (e.g., modularity ↔ depth currently -0.617).
- **evidence**: results.md line 10-37 score table shows: msgspec 10, click 17, requests 19, archy 19, flask 24, httpx 23, boto3 39, starlette 34, anyio 42, datasette 59, mkdocs 61, pytest 69, botocore 76, rich 100, pydantic 104, scrapy 172, mypy 195, sqlalchemy 255, setuptools 317, numpy 424, scikit-learn 638, ansible 581, dagster 801, django 902, pytorch 2252. The gap between 195 (mypy) and 255 (sqlalchemy) is a jump from 'large tool' to 'very large ORM'—nothing in the 200-300 range. Pytorch alone could be driving the modularity-depth correlation due to its extreme position (highest Q, lowest depth, highest module count).
- **impact**: Pearson correlation on a bimodal distribution with an outlier is sensitive to that outlier. If pytorch's 0.680 modularity and 0.286 depth are unusual for its size, the correlation r = -0.617 could be driven by that one point rather than a general relationship. This would make the axis-independence claim brittle: adding or removing one project shifts the evidence significantly.
- **fix**: Compute correlations with and without pytorch; report both and note sensitivity. Add 2-3 projects in the 200-400 module range to fill the gap. Compute partial correlation (controlling for module count) to separate size effects from axis effects.
- **notes**: This finding is fundamentally sound but needs refinement:

**What's real**: The bimodal distribution and 200-400 gap are genuine. PyTorch does shift correlations measurably. The sensitivity claim has merit but is overstated.

**What's missing from docs**:
1. Published sensitivity analysis showing Δr values with/without PyTorch (the correlation deltas computed here are not in bench/results.md or docs)
2. Partial correlation controlling for module count—would cleanly separate "size effect" from "axis effect" and strengthen the independence claim
3. Explicit acknowledgment that PyTorch's unusual metric combination (high Q + low depth, opposite the trend) is what drives the correlation tightening, not size alone

**Actionability**:
- The ticket should ask for: (a) correlation deltas published in results.md or RESEARCH_METRICS.md, (b) partial correlation analysis (PyTorch module count as control variable), (c) clarification that OECD redundancy threshold (|r|=0.7) remains comfortable even with PyTorch.
- The corpus-filling suggestion (add 200-400 projects) is reasonable but lower priority—current threshold compliance is robust even without it.

**No threat to claims**: The axis-independence argument survives this scrutiny. The OECD 0.7 threshold is nowhere near breached (all correlations are ≤0.617). The geometric-mean design rationale does not break. However, documenting the sensitivity would strengthen credibility and transparency.

### [minor] Moderate axis coupling (depth at -0.617 and -0.581 with other axes) downplayed as passing threshold  (confirmed)
- **loc**: SCORING.md:429-461, results.md:43-52
- **claim**: Two of ten axis pairs (modularity ↔ depth and acyclicity ↔ depth) have |r| ∈ [0.5, 0.7], classified as 'moderate' coupling by OECD standards. SCORING.md explicitly acknowledges these as 'moderate' (line 430) but then defends them by noting they remain below 0.7. However, the moderate coupling is structurally concerning because it suggests depth is partially dependent on modularity/acyclicity, not fully independent.
- **evidence**: SCORING.md line 429-434: 'Two of ten sit at "moderate" coupling (`|r| ∈ [0.5, 0.7]`), both involving the original four axes (modularity↔depth and acyclicity↔depth).' Then line 442-443: 'deeper graphs tend to have higher modularity. Plausible: in a deep DAG, communities form along the chain naturally...' This is a POST-HOC explanation for why the coupling exists, not a rebuttal of the concern. If the explanation is correct, then the axes are confounded by a common cause (DAG structure), which means optimizing one affects the others—violating the independence assumption.
- **impact**: The geometric mean's non-compensatory property (SCORING.md line 349-352) depends on axis independence. If depth optimization tends to lower modularity (as the negative correlation suggests), then the score is partially compensatory in those two dimensions: improving one drags down the other. This weakens the claim that 'improving overall requires improving every axis' because two of the axes are coupled through DAG structure.
- **fix**: Either (1) redesign the depth axis to be uncorrelated with DAG community structure (e.g., by weighting by SCC size rather than simple longest-path), or (2) acknowledge the moderate coupling in the design rationale and explain why it's acceptable (not just that it's below 0.7). Compute partial correlations controlling for module count to separate size effects.
- **notes**: The moderate coupling between depth and the other axes is CONFIRMED as real and acknowledged in the codebase. However, this is not a deficiency in current documentation—it's an intentional, well-documented tradeoff supported by empirical study.

Key nuances for any ticket:
1. The coupling is not a design failure but an observed structural property of the benchmark
2. Extensive testing (21 candidate reformulations across two axes + 6 aggregator variants) concluded that fixing the coupling requires either sacrificing actionability (can't distinguish "new long chain" from "new SCC") or shaking the leaderboard substantially (Spearman ρ = 0.53-0.64)
3. The real safety property comes from the non-compensatory aggregation protecting the other four axes (modularity, acyclicity, equality, complexity), which all have non-trivial correlation with overall
4. Depth's weak correlation with overall (|r| ≤ 0.187) means the coupling is operationally irrelevant for gaming—depth optimization barely moves the score regardless
5. The suggested action (partial correlations controlling for module count) could provide additional insight but per the empirics analysis is unlikely to change the conclusion

If actionable improvements are desired, the ticket should focus on: (a) documenting the design rationale more explicitly (e.g., why actionability was chosen over perfect independence), or (b) reconsidering the PGM aggregator option mentioned in SCORE_SHAPE_REDESIGN_EMPIRICS.md line 278-286 (which fires a penalty "exactly when correlated axes diverge"), which achieved ρ = 0.907 rank stability while directly addressing the correlation imbalance.

### [minor] Complexity axis calibration sensitive to CC floor: divisor widened v5→v8 because metric was too harsh  (confirmed)
- **loc**: SCORING.md:260-291, bench/run.py:238, results.md
- **claim**: The complexity axis mapping cc_mean to [0,1] was recalibrated from `/5` (v0.20-v0.22) to `/8` (v0.23+) because validator/parser-heavy codebases with cc_mean ∈ [6, 9) were zeroing the geomean entirely. This suggests the original formula was poorly calibrated, and the benchmark (which includes no validator/parser projects to trigger the problem) never caught it.
- **evidence**: SCORING.md line 285-291: 'The divisor was widened from `/5` (v0.20) to `/8` (v0.23) after the original calibration drove the geomean to 0.000 on realistic backends whose `cc_mean` lands in `[6, 9)` (validator-heavy or parser-heavy codebases).' This change happened because real-world feedback revealed the formula was broken, not because the benchmark caught it. The 28-project corpus (results.md line 101-132) has no projects with cc_mean >= 6, so the benchmark couldn't have detected this issue.
- **impact**: The complexity axis is empirically calibrated to the corpus, not to the full space of Python codebases. If a new project type (crypto validator, regex engine, DSL parser) is analyzed, it may have cc_mean in the [6,9) range and produce unexpected scores. The corpus is insufficient to validate the full operating range of the metric.
- **fix**: Add projects known to have high cc_mean (e.g., sqlalchemy shows 2.45, but there are backends with 8+) to the corpus. Extend complexity testing to cover the full theoretical range [1,9], not just the 1.77-5.33 range observed in the current corpus. Consider a more robust calibration (e.g., percentile-based rather than hard-coded divisor).
- **notes**: CONFIRMED: The complexity axis calibration was overfitted to the benchmark corpus and failed on real-world projects. Key scope notes:

1. DISCOVERY TIMELINE: Issue was discovered 5 hours after v0.20 release via external project "governingdocs/backend", not through benchmark validation. This suggests inadequate pre-release testing on diverse Python codebases.

2. SPECIFIC MISSING COVERAGE: The corpus lacks projects known to have high cc_mean:
   - Current max: msgspec 5.33
   - Problematic range discovered: [6, 9)
   - Examples of high-cc_mean domains: crypto validators, parsing libraries, DSL compilers
   
3. CALIBRATION JUSTIFICATION CHANGE: The v0.20 docs claimed "without bottoming out at 0 for any real Python project" but this was incorrect - real projects do have cc_mean >= 6. The fix was not a "nice-to-have" but a correctness issue discovered in production.

4. BENCH EXPANSION SINCE: The 28-project corpus (as of v0.24) added pytorch (2,252 modules) for size diversity but still lacks domain diversity in complexity metrics. Consider:
   - At least one validator library (pydantic exists but at 3.62)
   - At least one parser/compiler (none currently)
   - At least one crypto implementation (none currently)
   
5. THE FIX PRESERVES BENCH ORDERING: The /5→/8 change is not arbitrary; it maintains ordering of all 27 existing projects while preventing zero-out for out-of-distribution cases. This is pragmatic but indicates the original tuning was empirically constrained.

Recommendation: Add at least 3 projects with cc_mean in [5.5, 8.0] to the benchmark to make this edge case discoverable in future iterations, and document that complexity calibration is specifically validated only over observed [1.77, 5.33] range with acknowledged extrapolation to 9.0.

## bench / dsm-bench


### [minor] block_comm tautologically gates on modularity's own partition, making correlation circular  (confirmed)
- **loc**: bench/dsm.py:107-130, line 186
- **claim**: block_comm is claimed as a distinct signal measuring whether edges respect community boundaries, but it gates on the partition produced by the exact same greedy_modularity_communities algorithm that the modularity axis uses.
- **evidence**: Line 110 in _community_blocks calls nx.community.greedy_modularity_communities(graph) — identical to archy.score.compute_modularity (src/archy/score.py:205). The bench then measures what fraction of edges stay within those communities. This is tautological: the partition is built to maximize within-community edges by construction, so high correlation (r=0.716) is inevitable, not evidence of orthogonality.
- **impact**: block_comm is not a new signal; it is a nested question ('does the graph respect the partition that modularity-maximization creates?'). Using it alongside modularity is like measuring 'are edges cliquish?' and 'how cliquish are the detected cliques?' — they measure the same thing, making the bench's conclusion of distinctness misleading.
- **fix**: Either use a different community-detection algorithm for block_comm (Leiden, Louvain) to measure against an independent partition, or remove block_comm and document that the three remaining signals (feedback, bandwidth, block_layer) remain under evaluation.
- **notes**: The circularity is real and empirically confirmed. However, it's already been identified, studied under the OECD discriminant-validity framework, and explicitly rejected from production. The ticket should note: (1) the bench/dsm.py script is a research artifact that tested four DSM signal candidates and two (including block_comm) failed validity gates; (2) the decision to ship DSM visualization-only is documented and intentional; (3) no actionable change is needed for the production score. If the ticket is about documentation clarity, the scope should be: update bench/dsm.py comments to clarify that the script is an exploratory study whose results led to DSM-visualization-only design, and optionally cross-reference DSM_EMPIRICS.md for readers wanting to understand why block_comm was rejected. The suggested action of "use Leiden/Louvain instead" was not pursued because the team chose visualization-over-scalar rather than algorithm-switching.

### [minor] Comparing DSM signals only against six existing axes masks whether they are linear combinations of those axes  (confirmed)
- **loc**: bench/dsm.py:234-256; bench/dsm_results.md:49-54
- **claim**: The bench compares each DSM signal against six existing axes and concludes orthogonality if all six correlations are |r| < 0.7. This subset-comparison strategy hides multicollinearity: a signal can have low pairwise correlations yet be a linear combination of the axes.
- **evidence**: Example: bandwidth has r=+0.603 with equality and r=-0.083 with depth. Both pass individually, but bandwidth could be 0.80*equality + 0.20*depth (redundant to their combination) while having no individual high correlation. The bench never runs hierarchical regression or VIF (variance inflation factor) to detect such multicollinearity.
- **impact**: The bench's conclusion that the four DSM signals are distinct might overstate their independence. If they are actually linear combinations of the existing five axes, they add zero new information, even though pairwise correlations are all below 0.7. This is the 'comparing a SUBSET of output fields hides bugs' failure mode.
- **fix**: Run hierarchical regression: do the four DSM signals explain additional variance in a real outcome (defect count, maintenance cost) *after* the five existing axes are entered? Compute VIF for each; VIF > 2-3 suggests redundancy. Run PCA on all nine signals to count true independent dimensions.
- **notes**: 
FINDING IS TECHNICALLY SOUND BUT LOW PRACTICAL IMPACT:

1. The bench methodology IS incomplete: pairwise |r| < 0.7 cannot detect multicollinearity via linear combinations. This is a valid statistical concern.

2. However, the incomplete methodology has NOT led to a shipped bug:
   - The bench is documented as exploratory
   - The four DSM-derived signals were REJECTED as unsuitable (per DSM_EMPIRICS.md)
   - DSM is shipped as visualization-only, not as a score axis
   - The document explicitly explains why (even the two that pass discriminant validity fail other criteria: contested direction, no refactoring action, poor intuition)

3. The bench's own data contradicts its own |r| < 0.7 rule for 2 of 4 signals (block_comm and block_layer exceed the threshold), which the document acknowledges.

4. SUGGESTED SCOPE FOR TICKET:
   - Is this purely a documentation/methodology clarification? (Add a note that "distinct signal" means "passes pairwise threshold" not "guaranteed independent")
   - Or verify that the inter-axis correlations themselves don't exhibit problematic multicollinearity (compute VIF on the six axes)
   - Consider adding a small sidebar in DSM_EMPIRICS.md noting that multicollinearity among the six existing axes was not tested, and bandwidth could theoretically be a linear combination even with |r| < 0.7 pairwise

SEVERITY DOWNGRADE RATIONALE:
- Claimed as "major" but bench is not shipping a wrong conclusion (DSM scalars are rejected)
- The methodology gap is real but doesn't affect the final decision
- Downgrade to "minor" (methodology documentation gap) rather than "major" (wrong shipped behavior)


### [minor] Feedback correlates strongly with acyclicity (r=-0.688), suggesting it primarily measures what acyclicity already captures  (uncertain)
- **loc**: bench/dsm.py:79-104; bench/dsm_results.md:51
- **claim**: Feedback is claimed as a distinct signal measuring 'how much of the graph violates a clean layered shape,' but r(feedback, acyclicity) = -0.688 shows high correlation, suggesting it is mainly a restatement of acyclicity.
- **evidence**: Feedback measures the fraction of edges above the diagonal (backward in topo order), which are hallmarks of cycles. Acyclicity measures the fraction of nodes in cycles. Both measure 'how much does the graph have cyclic violations,' just via different metrics. The r=-0.688 (negative because feedback increases while acyclicity decreases with cycles) confirms they are nearly the same signal.
- **impact**: An agent using feedback and acyclicity together thinks it is diversifying its cyclic-risk assessment, but they are highly correlated (r=-0.688 in absolute value). The agent overweights cycle risk and misses other dimensions of quality.
- **fix**: Reframe feedback as a DSM *layout* diagnostic (useful for localizing cycles within the DSM ordering) rather than as a new architectural axis. Or use feedback and bandwidth together (r=-0.548 between them, more independent) as a joint DSM visualization tool rather than separate scoring metrics.
- **notes**: The correlation between feedback and acyclicity is real but not as simple as "measuring the same thing." The ticket should capture:

1. The correlation is genuine (r≈-0.69 to -0.85) but NOT primarily evidence of redundancy - it's partly due to methodological asymmetry (full vs internal subgraph) and floor effects (many projects have feedback≈0 and acyclicity≈1)

2. The feedback metric, while correlated with acyclicity, provides a distinct measurement approach (edge-based vs node-based) that could be valuable for understanding different aspects of graph structure

3. The analogy to block_layer is instructive: feedback and block_layer are highly correlated (0.974) yet measure different things, showing that correlation alone doesn't prove redundancy

4. If feedback is to be retained, document it as a complementary DSM diagnostic (useful for localizing cycles within ordering) rather than as an independent architectural axis. Alternately, pair it with bandwidth (r=-0.548, more independent) for joint DSM visualization

5. The suggested reframing in the finding (using feedback for layout diagnostics rather than as an axis) has merit regardless of the correlation strength

The severity should be "minor" because the correlation, while notable, doesn't conclusively demonstrate that feedback is redundant - only that it correlates with one existing metric while measuring it in a different way.

### [minor] Bench corpus of 27 pinned projects may not generalize; results could shift with new codebases or Python evolution  (uncertain)
- **loc**: bench/dsm.py:210; bench/dsm_results.md:3
- **claim**: The bench measures correlations on a fixed snapshot of 27 pinned projects (bench/projects.yaml). Generalization to new codebases or future Python ecosystems is unvalidated.
- **evidence**: The results are specific to the 27 projects captured locally at the time of bench run. If Python patterns shift (e.g., more type hints, async/await, new frameworks), the correlation structure could change, and the bench's 0.7 threshold verdict might not hold on a new corpus.
- **impact**: Low, as this is standard for empirical benches. However, the 0.7 threshold and 'distinct signal' verdict are calibrated to these 27 projects specifically, not a universal principle. Overgeneralizing to new projects carries hidden risk.
- **fix**: Document that the bench is a point-in-time snapshot on these 27 projects. If DSM signals are promoted to a score axis in a future version, re-run the bench to confirm the correlation structure is stable.
- **notes**: The core issue is partially mischaracterized: the 0.7 threshold is NOT bench-calibrated; it comes from established OECD composite-indicator methodology. However, the broader concern about generalization risk IS valid and under-documented. Recommendations: (1) Add clarification to dsm.py or dsm_results.md that the 0.7 OECD threshold is standard methodology, not bench-specific. (2) Add a note to DSM_EMPIRICS.md section "What this analysis does not settle" (currently line 134) acknowledging that while the 27-project correlation structure is stable across the post-implementation sweep on the same projects, generalization to new codebases or evolving Python patterns is unvalidated. (3) If DSM signals are ever promoted to a score axis, add a roadmap item to re-run the bench on a new corpus to confirm correlation structure stability.

## bench / hotspots-bench


### [major] Stale_frac is a set-difference metric, not a churn measurement  (confirmed)
- **loc**: bench/hotspots_sweep.py:156-158, hotspots.py:23-31
- **claim**: The metric `stale_full_frac = |full_top_20 - 12mo_top_K| / |full_top_20|` is presented as measuring 'recency contamination' (complex-but-dead files). But it only measures set difference, not actual churn. A module can be absent from 12mo top-K for three reasons: (1) it's dead (zero recent churn), (2) it was fixed/refactored so recent churn is zero by design, (3) recent churn is non-zero but low enough that other modules ranked higher.
- **evidence**: The metric computes set operations on module names, not churn values. The code at hotspots.py:26-31 states: 'A complex but stable file is load-bearing but tolerable' — this is interpretation (2), where zero recent churn is the GOAL, not a failure mode. Yet stale_frac treats it as bad. The bench never validates the actual churn of modules in (full_top_20 - 12mo_top_K) to distinguish which case applies.
- **impact**: The 'stale_frac' metric could be misleading users. A high stale_frac (0.25 median) might mean: (a) 25% of hotspots are dead code (bad), (b) 25% of hotspots are well-engineered and stable (good), or (c) a mix. Without looking at actual churn values, users cannot tell.
- **fix**: Rename the metric to avoid the 'stale' framing, or compute it correctly by checking the actual git churn of modules in the difference set. Add a validation step: for projects where stale_frac > 0.5, inspect the actual 12mo churn of those modules and report whether they are truly zero-churn (dead) or just lower-churn (stable but active).
- **notes**: The metric name and documentation are misleading. Either: (1) Rename to avoid "stale" framing (e.g., `not_in_12mo_frac` or `window_drift_frac`) to reflect what it actually measures (set difference), or (2) Implement the claimed metric by extracting actual 12-month churn values from the hotspot results and checking which modules in (full_mods - twelve) have zero 12-month churn vs. non-zero. The bench has access to full churn data from the Hotspot objects but discards it. A validation step should be added: for projects where stale_frac > 0.5, sample the actual 12mo churn of modules in the difference set and report the breakdown (zero-churn vs. lower-churn). Example problematic case: mkdocs at 1.00 stale_frac (line 31) - the single 12mo hotspot means 100% of the full top-20 are "stale", but this could mean they're all dead or all just ranked #21-40 with non-zero churn. Documentation should clarify the three-case ambiguity and the contradiction with hotspots.py's "complex but stable = good" principle.

### [major] Archy self-entry (v0.22.0) is a poisoned control with J=1.00  (confirmed)
- **loc**: bench/hotspots_results.md (row: archy | v0.22.0 | 20 | 20 | 20 | 1.00 | 1.00 | 1.00 | 0.00)
- **claim**: Including archy itself (pinned to a release tag v0.22.0) in the benchmark inflates the correlation metrics. The entry shows perfect overlap (J=1.00) and zero stale fraction. This is not evidence that 'full history is a good default' — it's evidence that 'a frozen codebase has stable hotspots.'
- **evidence**: The comment in projects.yaml states: 'Pin to a release tag, not HEAD: every merged commit was silently shifting archy's row in the score table...' This is a snapshot, not a living project. Of course the hotspots are identical across windows when the code was released months ago. The median J=0.600 includes this inflating datapoint.
- **impact**: The benchmark's median metrics are biased upward by one datapoint that is not representative of real active projects. An external observer reading the results cannot tell that one of 27 projects is a frozen snapshot.
- **fix**: Either (a) exclude archy from the median calculation and report it separately as 'dogfooding (pinned snapshot),' or (b) compute archy's hotspots at *two different points in time* (e.g., two release tags, or HEAD at two different months) and measure the cross-time J to validate temporal stability rather than within-snapshot stability.
- **notes**: The finding correctly identifies that archy's benchmark entry uses a fundamentally different measurement than the other 26 projects. The suggested remedies are sound:
- Option (a): Report archy separately as "dogfooding (pinned snapshot)" and exclude from median, with a note that the 0.60 median is across 26 active projects
- Option (b): Measure archy at two different release tags (e.g., v0.18.0 and v0.22.0) to compute cross-release Jaccard as a genuine temporal-stability test

The finding does NOT claim the reported median (0.60) is mathematically wrong — it claims the *interpretation* is compromised because one data point doesn't measure the intended concept. This is a research-methods issue, not a data-integrity issue. The fix should preserve reproducibility of hotspots_results.md (don't change the table) while clarifying the documentation about what was actually measured and whether archy's entry is directly comparable to the other 26.

### [major] No temporal validation: script doesn't test whether hotspots change across time  (confirmed)
- **loc**: bench/hotspots_sweep.py (entire script): only measures J across windows at a single point in time
- **claim**: The bench measures J(full, 12mo), J(12mo, 6mo), etc. at a single point in time (2026-05-15). It never measures J(full_may2026, full_march2026) or similar. This means the bench cannot validate whether 'stable across windows at time T' implies 'stable across time' — which is what a user actually cares about.
- **evidence**: The script runs hotspots on each project once, then computes pairwise Jaccard on the windows. There is no loop over multiple time points. A user wants to know: 'if I run `archy hotspots` every month, will the top-K set bounce around?' The bench cannot answer this because it only sampled one month.
- **impact**: The benchmark may give false confidence in the default. A default window that produces consistent results across (full, 12mo, 6mo) at a single point may still produce wildly different results at different points in time — the thing the user actually cares about.
- **fix**: Extend the bench to run at multiple time points (e.g., 2025-11, 2025-12, 2026-01, 2026-05) and compute temporal Jaccard J(t1_full, t2_full), J(t1_12mo, t2_12mo), etc. This would validate whether the default is temporally stable, not just window-stable.
- **notes**: The gap is real and the suggested action (loop over multiple time points like 2025-11, 2025-12, 2026-01, 2026-05) is valid and actionable. However, clarify in the ticket that the current benchmark validates window-size stability, not temporal stability. The question "will top-K bounce month-to-month?" is genuinely unanswered by the existing bench and is a distinct validation axis from "does window size choice matter?" If temporal stability becomes a design concern, the proposed extension (multiple time-point sweeps with temporal Jaccard) is the right approach.

### [major] No sensitivity analysis: TOP_K=20 is arbitrary and not validated  (confirmed)
- **loc**: bench/hotspots_sweep.py:42 (TOP_K = 20)
- **claim**: The bench uses TOP_K=20 without justification or sensitivity testing. Some projects produce exactly 20 hotspots (starlette, many others), suggesting either: (a) they genuinely have only 20, or (b) the default is capping at 20. If (b), then results for those projects are artifacts of the choice.
- **evidence**: Most projects in results.md report |full|=20, |12mo|=20, |6mo|=20. This suggests the result sets are being capped at the TOP_K. If the true hotspot count is higher, then Jaccard comparisons are comparing two truncations, not full rankings.
- **impact**: Users may not realize that the top-20 hotspots could change significantly if they run with --top 30 or --top 100. The bench doesn't validate that the window-choice conclusion holds across different K values.
- **fix**: Run a sensitivity analysis: compute J(full, 12mo) for multiple K values (K=5, 10, 20, 50) and show whether the conclusions hold. Document that the default K=20 was chosen empirically or arbitrarily.
- **notes**: This is a **CONFIRMED FINDING** with evidence of actual data capping. The bench is valid as-is *for comparing window effects at K=20 specifically*, but the issue ticket should capture:

1. **The bench measures artifact, not ground truth**: All hotspot counts in results.md are capped at TOP_K. To avoid user confusion, the results table should add a clarifying note: "All counts capped at --top 20; see JSON response 'total' field for full counts."

2. **TOP_K=20 lacks empirical justification**: Unlike the --since window choice (which has documented tradeoffs: median Jaccard, stale fraction, low-activity collapse), the K value was never tested. A sensitivity analysis is needed to either (a) validate that conclusions hold across K={5,10,20,50,100}, or (b) document the choice was arbitrary.

3. **Impact on users**: Users may assume "top 20 hotspots" means the top 20 most important files. In reality, for rich (154 true hotspots), they're seeing only the top 13% of the list. The --top parameter is documented as "Maximum hotspots to show" but not why 20 is the default.

4. **Recommended actions**:
   - (Minor fix) Update hotspots_results.md table header or caption to clarify "Shown top-20 (total column in JSON); rerun with --top N to see full ranking"
   - (Investigation) Run sensitivity analysis: sweep hotspots_sweep.py with K=[5,10,20,50,100] and check if median Jaccard / recency findings hold. If findings are robust across K, document this. If not, justify the K=20 choice or raise it.
   - (Documentation) Add to LEARNINGS.md v0.18.0 section: "Defaulting to top-20 was chosen as a practical limit (email/TUI display width); no sensitivity analysis validates this choice against alternatives. Use --top N to retrieve full ranking."

5. **Severity is MAJOR not MINOR** because: (a) the benchmark measures capped truncations, not rankings (both the bench's Jaccard and users' results are artifacts of K), and (b) documentation/docstrings don't disclose this limitation, potentially misleading users about the stability of top-K conclusions.

### [minor] Core tautology: 'stable top-K' validates the default, not correctness  (uncertain)
- **loc**: bench/hotspots_sweep.py:1-31 (docstring), bench/hotspots_results.md (interpretation)
- **claim**: The benchmark claims to validate the default window choice (full history) by measuring Jaccard overlap and recency contamination. But it only measures whether results are *stable across different windows*, not whether the results are *correct* for actual refactoring prioritization.
- **evidence**: The script explicitly states: 'There's no labelled ground truth on the bench' and instead measures two proxies: (1) Jaccard overlap between window-specific top-K sets, and (2) fraction of full-history top-K not in 12-month top-K. Neither of these validates whether users should actually refactor the files on the hotspots list. The median J(full, 12mo)=0.60 simply means that 60% of modules overlap when comparing two windows — it does NOT validate that these modules are real refactoring opportunities.
- **impact**: The default window choice (full history) may be suboptimal or even wrong for active projects. A user gets a 'hotspots' ranking that claims to prioritize refactoring work, but lacks any validation that the ranking serves the user's actual goal (reducing technical debt, improving testability, etc.). An alternative hypothesis — that recent-churn hotspots (12mo or 6mo) are more actionable — cannot be ruled out.
- **fix**: Either: (a) acknowledge in the docstring that stability is orthogonal to correctness, and add a caveat that users should compare full-history hotspots against their own ground truth before trusting the ranking; or (b) run a small validation study where developers are asked to refactor top-5 hotspots from each window (full, 12mo, 6mo) and measure which produces better outcomes (fewer new hotspots, faster refactoring, better test coverage gains, etc.). Without this, the default is an educated guess, not evidence-based.
- **notes**: RECLASSIFIED from blocker to minor. The bench itself is honestly scoped: it validates window choice stability, not correctness. The real gap is user-facing clarity. Suggested fixes: (1) In CLI help/SIXTY_SECOND_TOUR, add a caveat that "hotspots" is a heuristic based on CC × churn (Tornhill 2015) and users should validate against their refactoring outcomes; (2) In docstrings, elevate the "diagnostic" label so it reaches the user (not just code comments); (3) Optionally, note that recent-churn hotspots (`--since 12.months`) may be more actionable for active projects. Not a correctness bug - the design is defensible - but needs documentation clarity to set proper expectations.

### [minor] Low-activity projects confound the 'stability' metric  (confirmed)
- **loc**: bench/hotspots_results.md (rows for httpx, mkdocs, click, etc.)
- **claim**: Projects with incomplete 12mo result sets (e.g., httpx: 12mo=10, mkdocs: 12mo=1) automatically produce low Jaccard values and high stale_frac. The bench does not distinguish between (a) 'window choice is genuinely important because the project is mixed-activity' and (b) 'low J and high stale_frac are artifacts of project inactivity.'
- **evidence**: Active projects (|12mo|=20, i.e., have >=20 hotspots in recent window): median J(full,12mo)=0.60, median stale_frac=0.25. Inactive projects (|12mo|<20): median J(full,12mo)=0.35, median stale_frac=0.60. The inactivity level is a strong confound. mkdocs (|12mo|=1) has J=0.00 and stale=1.00 — which is just mathematical consequence of having one file in 12mo, not evidence of window-choice importance.
- **impact**: The global medians (J=0.600, stale=0.250) are misleading because they aggregate over projects with vastly different activity levels, without stratification. A user cannot tell: 'does full-history work well across all projects?' vs. 'does it only work well on actively-developed projects?'
- **fix**: Stratify results by project activity: show J and stale_frac separately for active (|12mo|=20) vs. inactive (|12mo|<20) projects. Explain that low J and high stale_frac on inactive projects may reflect inactivity, not window-choice failure. Consider excluding projects with |12mo|<20 from the median calculations, or report both medians (all projects vs. active-only).
- **notes**: The bench/hotspots_results.md report should be updated to: (1) Stratify J(full,12mo) and stale_frac by activity level (active: |12mo|=20 vs inactive: |12mo|<20), showing both median sets; (2) Add a footnote explaining that inactive projects with low |12mo| produce mathematically-driven low J and high stale_frac due to small sample size, not window-choice failure; (3) Clarify whether the global medians (J=0.600, stale=0.250) are meant to apply to all projects or only active ones. NOTE: The underlying issue is already acknowledged in LEARNINGS.md (lines 168-169) and hotspots.py docstring (lines 24-31), so the ticket is a documentation/presentation fix to the bench report, not a hidden architectural problem. The code correctly defaults to full history and documents the recency-contamination tradeoff. Suggest: update hotspots_results.md with a "Activity level analysis" section showing the stratified medians and explaining the confound, similar to how the text was explained in LEARNINGS.md.

### [minor] Implicit assumption: 'if windows agree, default is correct' is unvalidated  (confirmed)
- **loc**: bench/hotspots_sweep.py:8-10 (docstring comment: 'If the window doesn't move the top-K set, the default doesn't matter much')
- **claim**: The script's opening statement assumes that window stability implies the default choice doesn't matter. But this is only true if the goal is 'make a decision that's robust to window choice.' If the goal is 'choose the window that best predicts refactoring outcomes,' then stability is irrelevant — correctness is all that matters.
- **evidence**: The docstring says: 'If the window doesn't move the top-K set, the default doesn't matter much.' This inverts the logic. If the default is full-history and windows DON'T move the set, it could mean: (1) good, any default works, OR (2) bad, we haven't tested the full-history default's correctness. The bench cannot tell.
- **impact**: Users may be misled into thinking that J=0.60 is evidence that the default is good. It only shows window-stability within this one snapshot, not default-correctness.
- **fix**: Reword the opening docstring to clarify: 'This script measures whether different windows produce similar top-K sets. HIGH Jaccard means results are window-stable (less sensitive to recent vs. historical data). However, window-stability does NOT validate that the results are correct — i.e., that users should actually refactor the files on the list. To validate correctness, this script would need ground truth labels or a longitudinal study of refactoring outcomes.'
- **notes**: The docstring conflates two concepts: (1) window-stability (a robustness metric) and (2) default-validity (a correctness claim). The Jaccard measurements in the output (median J(full,12mo) = 0.600) show agreement rates, not correctness validation. The suggested fix is to explicitly clarify in the docstring that high Jaccard means "results are robust to window choice / less sensitive to recent vs. historical data" but that "window-stability does NOT validate that the results are correct — i.e., that users should actually refactor the files on the list." To validate correctness would require ground truth labels (files that developers actually should refactor) or longitudinal outcome tracking. This is documentation clarity, not a behavioral bug, but it could mislead users into misinterpreting benchmark results as evidence that the full-history default is validated when only its robustness has been tested.

### [nit] Jaccard is inflated when comparing sets of different sizes due to union denominator  (uncertain)
- **loc**: bench/hotspots_sweep.py:104-110 (jaccard function)
- **claim**: For projects where |12mo| < TOP_K (e.g., httpx with 10 vs. 20), Jaccard conflates two signals: (1) overlap proportion, and (2) result-set incompleteness. When one set is much smaller, the Jaccard can look high due to the smaller set, even if the large set changes a lot.
- **evidence**: Example: if full={A,B,C,...,T} (20 items) and 12mo={A,B,C,D,E} (5 items), then J = 5/(20+5-5) = 5/20 = 0.25 even though the overlap is 100%. This conflates 'both windows agree on top-5' (good) with 'we're missing items in 12mo' (neutral/expected). The metric doesn't distinguish.
- **impact**: Projects with low activity (small 12mo result sets) may report misleading Jaccard values. The metric becomes more about 'did we get enough hotspots?' than 'do the windows agree?'
- **fix**: Consider reporting intersection size and union size separately, not just Jaccard. Or use Jaccard on the *common size* (e.g., J(top_5_full, top_5_12mo)) to make the comparison fair. Document that Jaccard is size-dependent.
- **notes**: The finding correctly identifies that Jaccard is size-dependent and smaller result sets require more context to interpret. However, this is a well-understood limitation already documented in LEARNINGS.md and the code already provides the necessary context (explicit set sizes and stale_full_frac metric).

If any action is taken, it should focus on:
1. Clarifying documentation that Jaccard should always be read WITH the set sizes (|full|, |12mo|), not in isolation
2. Emphasizing that stale_full_frac is the primary recency-contamination signal, not Jaccard
3. Consider adding a note in bench/hotspots_results.md explaining that on low-activity projects (|12mo| < TOP_K), Jaccard can look low even with good overlap—refer readers to the set-size columns

The metric itself does not need to change. The benchmark already produces excellent results (median J=0.60 full-vs-12mo, matching the documented finding that "the window genuinely matters"). The issue is one of reader interpretation, not metric design.

## bench / inloop_prevalence-bench


### [major] Score regression detection gated on noise-level precision (1e-9) masked by 1e-5 rounding  (confirmed)
- **loc**: bench/inloop_prevalence.py line 197, bench/inloop_prevalence_results.json, bench/inloop_prevalence_results.md
- **claim**: The script flags a commit as 'score_regression' if overall < before - 1e-9, but rounds the reported delta to 5 decimals (1e-5). This creates a phantom 'regression' category: 158/315 regressions (50%) have |delta| < 1e-5 and thus round to 0.0 in output, yet are flagged as failures. The measurement precision (±1e-5) is 10,000x larger than the detection threshold (1e-9).
- **evidence**: Line 197: score_reg = after['overall'] < before['overall'] - 1e-9. Lines 214-215 round to 5 decimals. Data: 315 score_regs marked as regressions; 158 have overall_delta between -1e-9 and -1e-5 (invisible in output). Example: pydantic/pydantic f0d8f6593e marked score_regression=true with overall_delta=-0.0 (rendered zero).
- **impact**: The headline '29.4% score regression rate' is theater. The benchmark gates on floating-point slop that is invisible in the metric output itself. This violates the audit principle: a validation that gates on the thing it tests is tautological. Callers think 29% of commits regress by a measurable amount; actually most are sub-precision noise. The docs buried the caveat ('98% < 0.005') in Finding 3, subordinating it to the larger 29.4% claim in the results table.
- **fix**: Either (a) remove score_regression from the headline metrics and only report it as trend signal with explicit noise floor (≥0.001 in magnitude), or (b) set the detection threshold to match the rounding precision (1e-5 or higher) so what is flagged is also visible in output.
- **notes**: CONFIRMED CORE FACTS:
- Line 197: score_reg threshold is 1e-9 (magnitude > 1e-9 triggers flag)
- Lines 214-215: output rounded to 5 decimals = 1e-5 precision
- Data: 158/315 (50%) of flagged regressions have |delta| < 1e-4 (sub-noise)
- Example verified: pydantic f0d8f6593e shows delta=-0.0 despite being flagged
- 310/315 (98%) have |delta| < 0.005

PRECISION MISMATCH IS REAL:
- Phantom window exists: deltas between 1e-9 and ~5e-6 are flagged but round to ±0.0
- This violates measurement discipline: what triggers a gate should be visible in output

CAVEAT LOCATION (mitigating factor):
- Documentation DOES state the caveat in Finding 3: "98% dropped by less than 0.005"
- But caveat is NOT in the headline results table
- Readers of only bench/inloop_prevalence_results.md (the quick reference) see "29.4%" without context

RECOMMENDED SCOPE FOR TICKET:
1. Co-locate the noise-floor caveat with the 29.4% metric (in the table or immediately below)
2. Either (a) remove score_regression from headline metrics, or (b) increase detection threshold to match display precision (1e-5), or (c) clearly label it as "trend only, not actionable"
3. Add guidance note on interpreting the rate (e.g., "Of these drops, 98% are < 0.005 and visible as noise-level in output")

SEVERITY JUSTIFICATION:
Not blocker: The numbers are mathematically correct and documented (in detailed form). The real signal (0.5% cycle intro) is accurate and unaffected. Blocker would require false statistics or hidden data.
Major: The presentation creates genuine misleading impression at surface level (29.4% suggests structural instability when it's mostly noise). Half of flagged regressions are invisible in their own output. Precision mismatch violates audit principle. Fix requires either code change (threshold adjustment) or clear presentation change (caveat visibility).

### [major] Main prevalence claim (0.5% cycle regression) rests on N=5 events, understating uncertainty  (confirmed)
- **loc**: bench/inloop_prevalence_results.md lines 34-37, bench/inloop_prevalence.py lines 256-265
- **claim**: The study claims cycle regressions occur in ~1 of every 200 commits (0.5%) and is 'stable' at this rate (0.6% at N=360, 0.5% at N=1072). But the actual event count is only 5. With N=5, the binomial 95% CI spans [0.2%, 1.3%], and 'consistency' between two measurements using the same repos/window is not an independent replication.
- **evidence**: Cycle regressions: 5 out of 1,072 commits. Docs claim stability but both the N=360 and N=1,072 runs sample from the same 11 repos and same git history (default branches). No cross-repo stratification or out-of-sample test. Statistical power is weak; 2-3 additional events would swing the rate to 0.7%, contradicting the stability claim.
- **impact**: The framing 'archy is built to catch rare but severe multi-module cycles' relies on 0.5% being a trustworthy estimate of the true prevalence. With N=5, the estimate is brittle. A larger corpus (different repos, different time windows, or enriched for refactors/large changes as the study proposes for future work) could easily land at 0.2% or 1%. This affects the value proposition: if the true rate is 0.2%, archy's rare-fires case is weaker; if 1.3%, stronger. The uncertainty is not quantified in the headline results.
- **fix**: Report 95% confidence intervals alongside point estimates (0.5% [0.2-1.3%]). Acknowledge that power is low; frame 0.5% as a point estimate requiring replication on larger or distinct corpora. Consider the Q1b agent study as the actual test, not this human base-rate study.
- **notes**: 1. The main prevalence claim (0.5%) rests on N=5 true events. The 95% CI is [0.2%, 1.1%], making the point estimate substantively uncertain.
2. The claim that the rate is "stable" across sample sizes is misleading: N=360 and N=1,072 are both extracted from the same sequential 1,072-row dataset, not independent runs. Both sample from the same 11 repos, same default branches, same git history.
3. There is no separate pilot study or out-of-sample replication in the codebase.
4. The documentation's "Honest limitations" section (lines 131-150) does acknowledge the small event count as a limitation ("not tightly estimated") and notes that "more events (a larger or refactor-enriched corpus) would sharpen it."
5. However, the headline results (lines 58-63) report "0.5%" and claim stability without quantifying the 95% CI or clarifying the non-independence of the two sample sizes.
6. The impact is real: adding 2-3 events would move the point estimate to 0.65-0.75%, contradicting the "stability" frame. The choice of which events to sample and the actual frequency in other corpus (e.g., the agent test set proposed for Q1b) could land anywhere in [0.2%, 1.1%].
7. Recommend: (a) report 95% CI in headline results; (b) clarify that N=360 and N=1,072 are non-independent (same corpus, different sample sizes); (c) rename "stable" to "consistent" or frame as "preliminary" pending Q1b agent study.

### [major] 29.4% score regression rate counts only improvements >0, creating asymmetric denominator  (confirmed)
- **loc**: bench/inloop_prevalence_results.md line 30, bench/inloop_prevalence.py lines 226-253
- **claim**: The summary table reports both 'cycle_reg' and 'score_reg' as regression rates. But in the data, 210 commits improved (19.6%), 314 regressed (29.4%), and 548 were flat (51.1%). The 29.4% does not compare to the improvement rate; instead, the docs frame it as a finding ('score drops are common') buried in Finding 3. This creates an asymmetry: positive changes are not highlighted, negative ones are.
- **evidence**: Data: 548 zero-delta, 210 positive delta, 314 negative delta. Summarization: 29.4% negatives reported prominently; positives (19.6%) not mentioned in prevalence table or per-repo breakdown. Docs say 'score drops are common (29%)' but could equally say 'score holds stable (51%)' or 'improvements are rare (19.6%)'.
- **impact**: The benchmark creates a misleading impression that commits regularly degrade structure (29% regression rate). But more than half are neutral, and improvements occur at 2/3 the rate of regressions. If the point is to measure headroom for archy, the relevant baseline is not 'negatives exist' but 'are negatives systematic or noise?' The asymmetry masks this. For a population health signal, mean ≈ 0 and median = 0 are more informative than 'more losses than gains'; the latter can be true in random walk data.
- **fix**: In the results table and Finding 3, report the full distribution: flat (51%), improve (19.6%), regress (29.4%), and the median/mean deltas. Avoid framing 29% as a prevalence finding without context on the 51% flat and 19.6% improvement rates.
- **notes**: The finding is substantively correct but requires clarification on scope:

1. **Confirmed Issue**: The summary tables and prevalence reporting show an asymmetry — 29.4% regressions are prominently reported per-repo while 19.6% improvements and 51.1% stable commits are not mentioned at all in the summary tables. The code's summarize() function actively omits counting improvements/flat cases.

2. **Context Consideration**: The research document does provide important nuance in Finding 3 (the 98% < 0.005 triviality analysis and median ~= 0 conclusion), which contextualizes the 29% as predominantly noise. However, this nuance appears *after* the 29% is framed as "common," not alongside it.

3. **Recommended Changes** (aligning with finding's suggestion):
   - In inloop_prevalence_results.md Key Numbers: add a line reporting the full distribution: "Score deltas: flat (51.1%), improve (19.6%), regress (29.3%)"
   - In INLOOP_PREVALENCE_EMPIRICS.md Results table: add a row or append notation: e.g., "Composite score flat (no material change)" with 51.4% to balance the 29.4% drop figure
   - In Finding 3 heading/opening: explicitly note the distribution before claiming drops are "common" — e.g., "Score drops are reported in 29% of commits, but over half show no material change and 20% improve. Of the 29% that drop..."

4. **Scope Note**: The finding accurately identifies presentation asymmetry in a regression-focused study. The study's actual purpose (measuring base rates of structural regressions, primarily gate signal rarity) does not require measuring improvements, but the public summary should reflect the fact that the majority of commits are neutral or improve, not just that a minority regress.

### [major] Silent sample attrition (28/1100 commits) with no reporting of failure modes biases toward parseable code  (confirmed)
- **loc**: bench/inloop_prevalence.py lines 130-137, 182-191
- **claim**: The script silently drops commits where build_graph() fails or returns no nodes. It drops 28/1100 configured samples (2.5%) without logging why. The docs mention requests suffered 28-commit loss due to src/ migration, but don't quantify failure modes per repo or analyze survivor bias.
- **evidence**: Script samples 100 per repo × 11 repos = 1100 total configured. Final rows = 1072. 28 dropped silently. Failures at line 134 (metrics() returns None) are caught but not logged. Per docs, requests got N=72 (not 100) due to src_dir migration skewing toward post-2023 code. No mention of build failures in other repos.
- **impact**: The sample may skew toward recent, well-structured code. If early commits in a repo had syntax errors or missing dependencies that broke at C^ but not C (or vice versa), those edges are lost. The prevalence estimate is conditional on 'commits that build cleanly at both parent and child,' not 'all commits.' This is honest for that population, but the denominator is not fully characterized. It could affect generalization to larger/older repos or codebases with less stable history.
- **fix**: Log and report failure modes: (a) failures at C due to build errors, (b) failures at C^ due to build errors, (c) missing src_dir path. Stratify the 1072 results by repo and failure rate per repo. Explicitly state prevalence is conditional on 'commits with valid graphs at both parent and child.'
- **notes**: The finding is fundamentally CONFIRMED but with nuanced scope. The ticket must capture:

1. CONFIRMED ISSUES:
   - Silent dropout without logging: 28/1100 commits (2.5%) dropped with no per-sample failure logging
   - All 28 losses concentrated in requests repo; other 10 repos show N=100 (no visible failures)
   - Code has three silent continue paths (lines 184-185, 187-188, 190-191) with zero instrumentation
   - No runtime log output distinguishing between checkout failures, metrics failures (build_graph returns None), or missing src_dir path

2. DOCUMENTATION STATUS:
   - The src/ migration bias for requests IS documented (empirics.md lines 146-149)
   - This documentation is in the "Honest limitations" section, not in the main Prevalence results
   - The results.md shows "requests: 72" vs other repos "100" but provides no explanation in that table
   - The main Prevalence table (empirics.md lines 54-63) does NOT state it is conditional on build success

3. IMPACT (verified as real):
   - requests likely skews toward post-2023 code (pre-src/ migration commits cannot be built)
   - Unknown whether other 10 repos had ANY failures (absence of visible dropout could mean zero failures OR successful rebuilds despite failed checkouts)
   - Prevalence numbers (0.5%, 29.4%) are valid for the 1072 commits that succeeded but may not generalize to full repo history for requests

4. SUGGESTED IMPROVEMENTS (from finding):
   - Add runtime logging of failure modes (a) checkout failures at C (b) checkout failures at C^ (c) build_graph returned None
   - Report per-repo dropout counts and reasons in summarize() output
   - Explicitly state in Prevalence results: "N=1072 of 1100 configured samples; prevalence conditional on commits with successful graph builds at both parent and child"
   - Quantify the requests bias: number of pre-2023 losses vs post-2023 losses

### [major] Cycle regression definition requires BOTH cycle_count rise AND new cyclic modules, which is overly conservative  (confirmed)
- **loc**: bench/inloop_prevalence.py line 193
- **claim**: cycle_reg = (cycle_count rose AND new_cyclic modules > 0). This avoids false positives from cycle swaps/relabelings, which is good. But it also misses cases where a new cycle is created but an old one is destroyed, netting zero in cycle_count. This is a conservative design choice, not a bug, but it underestimates true cycle regressions.
- **evidence**: Line 193: cycle_reg = after['cycle_count'] > before['cycle_count'] and len(new_cyclic) > 0. The 'and' gate means a commit that introduces a 3-module cycle but destroys a 4-module cycle (net count -1) would not trigger. No evidence in the data suggests this happened, but the definition is a floor on the true rate.
- **impact**: The 0.5% is an underestimate, possibly by 10-20% (rough guess; would need additional analysis). The real rate could be 0.5-0.7%. This is minor given the overall N=5 uncertainty, but worth noting if the rate is used for downstream decisions.
- **fix**: No action required; the conservative definition is intentional and documented. But consider running a sensitivity analysis: count cases where new_cyclic > 0 but cycle_count did not rise, and report both the conservative and expanded rates.
- **notes**: The finding should be promoted from "minor" to "major" because:

1. QUANTITATIVE SCOPE: The underestimation is not 10-20% as speculated—it is 340% (baseline 0.5% becomes 2.05% with just the new_cyclic criterion). This is significant enough to materially affect understanding of the prevalence question Q1a.

2. MISALIGNED RATIONALE: The documentation justifies the AND gate to prevent false positives from "cycle-for-cycle swaps" (destroying one cycle, creating another, net zero count). However, the 17 actual missed cases show cycle COUNT is stable or increases—these are NOT swaps, they're modules being pulled into existing tangled regions. The stated rationale does not match the actual effect.

3. SEMANTIC CORRECTNESS: A module becoming cyclic (new_cyclic > 0) is a genuine structural regression regardless of whether cycle_count increments. If modules A and B form a cycle, then module C joins it, we've gone from 1 SCC to 1 SCC but C was not cyclic and now is—that's a regression.

4. SCORE IMPACT: 4 of 17 missed cases also showed score regression (negative overall_delta), indicating the missed regressions have real quality implications.

5. REMEDIATION: The sensitivity analysis suggested in the finding (counting new_cyclic without the count-rise gate) should be run and reported. The actual rate should be reported as 2.05% or analyzed via a two-tier system (conservative 0.5% for strict new-cycle creation, expanded 2.05% for modules entering cycles).

SPECIFIC LOCATIONS TO UPDATE:
- bench/inloop_prevalence.py: Consider adding an optional flag to report both metrics
- docs/research/INLOOP_PREVALENCE_EMPIRICS.md: Acknowledge that the missed 17 cases are genuine regressions and not swap-related; update the rationale or report both numbers


### [minor] One cycle regression (requests, 8-module SCC) has POSITIVE overall score, contradicting 'severe' framing  (confirmed)
- **loc**: bench/inloop_prevalence_results.json (psf/requests 561e4b6889), bench/inloop_prevalence_results.md line 37
- **claim**: Of the 5 cycle-introducing commits, one (requests, 16 files, 8-module SCC) has overall_delta = +0.0121, i.e., the score improved despite introducing a large cycle. This contradicts the claim that 'when a cycle does appear it is a multi-module tangle... of the kind... compounds silently.' If the composite score improved, the cycle was not a dominant structural pathology in that commit.
- **evidence**: requests commit 561e4b6889: cycle_count 0→1, new_cyclic_modules 8, max_new_scc 8, overall_delta +0.0121, acyclicity_delta -0.13333. The acyclicity subcomponent penalized the cycle, but other factors (modularity, depth, equality, complexity) offset it enough to yield net improvement. The overall score (geometric mean of 5 factors) was not driven by acyclicity.
- **impact**: The 'severity' claim depends on the overall score being the right metric. But this event shows the overall score can improve while a cycle is introduced—the cycle is a minor side-effect of a larger beneficial refactor. This weakens the narrative that structural cycles are the main risk. It suggests the composite score is coarse and insensitive to cycles unless they dominate; archy already gates on cycle_count directly (via cycles.added), making the overall score signal redundant.
- **fix**: Investigate why the requests commit improved in score despite the 8-module cycle. Either (a) explain that this is a rare case of cycles being a side-effect of beneficial refactors (and thus not a blocker), or (b) acknowledge that the overall score is not the right metric for cycle severity and stop using it to characterize cycle regressions.
- **notes**: This is a documentation clarity issue, not a design flaw. The finding's core critique—that the overall score can improve despite cycle introduction—is valid and actually demonstrates that archy's design is correct to gate on cycles.added rather than on the composite score. However, the inloop_prevalence_results.md file's "Key numbers" section (line 36-37) frames the cycle events as "severe" and "multi-module" without mentioning that one case (requests, 8-module) actually improved the overall score. This could be clarified by either: (a) noting that the "severity" refers to the cycle structure itself, not to the score impact, or (b) explicitly mentioning the requests case as an example of beneficial refactoring that happened to introduce a cycle as a side-effect. The empirics document itself (INLOOP_PREVALENCE_EMPIRICS.md) already makes this distinction correctly in Finding 3.

### [minor] Composite score delta is symmetric around zero, consistent with noise rather than systematic degradation  (confirmed)
- **loc**: bench/inloop_prevalence_results.json, bench/inloop_prevalence_results.md Finding 3
- **claim**: Mean of all deltas ≈ 0 (0.000008), median = 0, stdev = 0.0014. Improvements and regressions are similarly distributed around zero. This is the signature of random walk / floating-point noise, not structural signal. The docs claim '98% of drops < 0.005 (noise floor)' but do not test whether the full distribution is consistent with random variation.
- **evidence**: Data: mean_delta=7.64e-6, median_delta=0, stdev=0.0014, count=1072. Distribution is roughly symmetric; 210 improvements with median +0.00018, 314 regressions with median -0.000095. If score were drifting downward due to structural rot, mean would be negative; it is not.
- **impact**: The overall score metric is not tracking systematic structural degradation. The 29.4% 'regression rate' is not evidence of a baseline level of structural damage archy should catch. It is evidence that the metric has noise floor of ~±0.0001 and is not stable for individual-commit decisions. The docs handle this correctly in Finding 3 ('98% trivial, gate should not fire on score') but the headline prevalence table and Finding 1 do not reflect it.
- **fix**: Rephrase Finding 1 and the results table to say 'new import cycles' (cycle_reg) are rare; drop or subordinate 'score drops' to a secondary metric that explicitly acknowledges it is a trend signal, not a per-commit gate. Provide mean/median deltas in the table to show the metric is centered near zero.
- **notes**: The finding is empirically sound. The statistics are verified. However, the severity should be MINOR rather than MAJOR for the following reasons:

1. The empirics document (Finding 3) ALREADY makes the correct interpretation - acknowledging the score is a trend signal, not a gate. The finding's suggested action is essentially asking for the results.md headline to be brought into alignment with what the empirics document already says.

2. The asymmetry is real but small (1.56x, which in absolute terms is 0.000956 vs 0.000613). This is still within the noise floor being discussed.

3. The core scientific claim (mean near zero, symmetric distribution) is accurate. Improvements are slightly larger on average, but this is a second-order effect that doesn't undermine the main conclusion that the metric is noisy for per-commit decisions.

4. The finding's suggested action is presentational/clarification work, not a correction of factual error. The ticket should ask to: (a) update results.md table/question to list "new cycles" as primary signal and "score drops" as secondary/trend-only, (b) add mean/median deltas to the table to show centering near zero, (c) optionally note the asymmetry (improvements 56% larger magnitude) as additional evidence of instability.

No rerun of the bench is needed - the data and Finding 3 interpretation are correct.

### [minor] Corpus skewed toward small, well-maintained projects; large and tightly-coupled repos excluded by design  (confirmed)
- **loc**: bench/inloop_prevalence.py lines 48-60, bench/inloop_prevalence_results.md line 139
- **claim**: The 11 repos are selected for 'build cost' and small-to-medium size. Django, SQLAlchemy, PyTorch (mentioned as intentionally excluded) are larger and more tightly-coupled, with more pre-existing cycles. The baseline prevalence in this corpus may not generalize to the large/legacy codebases where cycles are already endemic.
- **evidence**: Comments in script: 'small-to-medium pure-Python projects.' Docs: 'large, tightly-coupled repos (django, sqlalchemy, pytorch) were excluded for build cost. Those already carry more cycles in stock.' No repo in the sample has > 200 modules (flask ~100, pydantic ~100). Real production codebases at scale may have different cycle dynamics.
- **impact**: The 0.5% cycle-introduction rate is the baseline in well-maintained projects with low pre-existing cycle counts. In large legacy codebases, the rate might be higher (more tangles to introduce into) or lower (structure is already maxed out). The study does not test generalization. For archy's value prop (targeting agents on transformative changes in large codebases), the corpus is somewhat mismatched.
- **fix**: Acknowledge this as a limitation. If testing on large/legacy repos is possible (or planned), note it as a follow-up study. For now, the results apply narrowly to 'small-to-medium well-maintained projects,' and claims about agent PRs on large codebases should be qualified accordingly.
- **notes**: The finding is factually accurate. However, the ticket should note:

1. **The limitation is already documented**: The INLOOP_PREVALENCE_EMPIRICS.md research paper itself explicitly acknowledges this limitation in the "Honest limitations" section (lines 131-150). The authors already flag it as "Corpus is small-to-medium mature repos...with strong review cultures."

2. **The exclusion reason is stated**: The repos were excluded "for build cost" per line 140 - this is an engineering constraint, not a methodological one.

3. **The scope is clearly bounded**: The document's conclusion (lines 174-181) explicitly frames the results as applying to the specific corpus: "the results apply narrowly to 'small-to-medium well-maintained projects'" and the premise is that this is the "human-authored control base rate" (line 133).

4. **Agent-risk framing already acknowledged**: The research explicitly discusses how this relates to agents (lines 94-102) - noting that agent PRs are 154% larger than human PRs and thus enter the regime where cycles concentate.

5. **Q1b protocol already specified**: The authors have already designed the follow-up study needed on larger/legacy codebases (lines 151-169), gated on "a usage signal that agents will actually call the tools" (line 172).

The finding critiques something the authors already acknowledged and designed around. The question is whether this acknowledgment is sufficiently prominent in the main narrative claims about archy's value proposition and the 0.5% figure used in the paper's conclusions.

## bench / score_redesign-bench


### [major] Hardcoded rank-stability candidates exclude 4 out of 8 combinations meeting 0/10 gate  (confirmed)
- **loc**: bench/score_redesign.py:692-701 (interesting_combos hardcoded list)
- **claim**: The script reports rank-stability (Spearman ρ) for only 9 specific combinations, chosen by hand. Of these 9, only 4 actually meet the stated threshold of 0/10 moderate pairs. Meanwhile, 4 other 0/10 combinations are completely omitted from the rank-stability analysis — the section that drives the final decision. This gates the negative conclusion: the omitted combos include modular_tangle+depth_size_relative, feedback_x_tangle+depth_size_relative, log_cycle_count+depth_size_relative, and sentrux_legacy+depth_size_relative, all with 0/10 pairs.
- **evidence**: Cross-product table (lines 243-265, bench/score_redesign_results.md) shows 8 combinations with 0/10 moderate pairs. The rank-stability section (lines 690-707) reports only 9 combos total via hardcoded 'interesting_combos' list. Only 4 of those 9 have 0/10 (feedback_edges+scc_penalty, modular_tangle+scc_penalty, feedback_x_tangle+scc_penalty, feedback_edges+size_relative). The 4 omitted 0/10 combos never appear in rank-stability output, so their ρ values are never computed or reported.
- **impact**: The decision to reject redesigns is based on rank-stability data for only half the 0/10 candidates. The omitted combos are precisely those that combine poorly-performing acyclicity variants (log_cycle_count, sentrux_legacy) with depth_size_relative, or depth_size_relative with remaining variants. Without their ρ values, the reader cannot assess whether these 0/10 combinations also fail the actionability/rank-stability gate, or whether they would pass and offer a real alternative.
- **fix**: Either: (1) expand interesting_combos to include all 8 combinations with 0/10 pairs and compute ρ for all of them; or (2) explicitly filter the cross-product table to show only the 9 combos evaluated for rank-stability, and add a note explaining why the other 0/10 combos were excluded. The current state is theater: showing a full 21-combo cross-product table while reporting rank-stability on only a hand-picked subset, which masks incompleteness.
- **notes**: The finding accurately identifies a completeness gap in the analysis. The decision in the empirics doc explicitly relies on the claim that "0/10 combinations all shake up the leaderboard substantially," using this as evidence against axis redesigns. However, this claim is only empirically supported for 4 of the 8 identified 0/10 combinations. The 4 missing combos all involve `depth_size_relative` paired with four different acyclicity variants (modular_tangle, feedback_x_tangle, log_cycle_count, sentrux_legacy). Note: the empirics doc itself shows only 6 of these 8 in its cross-product table (with 2 marked "n/a" and sentrux_legacy completely omitted), which compounds the inconsistency between the raw results table and the human-written summary. The issue is not that these omitted combos *would* change the decision (their ρ values might indeed be high), but that their analysis is absent from a decision that claims to be based on universal behavior of all 0/10 combinations. The suggested fixes (either expand interesting_combos to evaluate all 8, or explicitly document the filtering criteria and why these 4 were excluded) are reasonable.

### [major] Documentation claims 6 combinations clear 0/10 but results show 8  (confirmed)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:143, bench/score_redesign_results.md:243-265
- **claim**: The empirics doc states 'Only six combinations clear 0/10 moderate pairs' but the cross-product results table shows 8 combinations with 0/10 (modular_tangle+depth_with_scc_penalty, modular_tangle+depth_size_relative, feedback_edges+depth_with_scc_penalty, feedback_edges+depth_size_relative, feedback_x_tangle+depth_with_scc_penalty, feedback_x_tangle+depth_size_relative, log_cycle_count+depth_size_relative, sentrux_legacy+depth_size_relative).
- **evidence**: bench/score_redesign_results.md lines 243-265: manual grep shows '/10' counts — 8 rows have '0/10', not 6. The doc's Table at line 147-154 lists only 6 rows (two with 'n/a' Spearman), but this is a curated subset, not the complete set.
- **impact**: The discrepancy creates confusion about how many combinations actually meet the OECD gate. A reader checking the doc's claim against the results table finds the claim unsupported. This suggests the doc was written before final data collection, or the cross-product table was regenerated and the doc summary wasn't updated.
- **fix**: Update doc line 143 to: 'Eight combinations clear 0/10 moderate pairs' and add a note explaining that the table below shows only the six with computed Spearman ρ values (the other two lack rank-stability data).
- **notes**: This is not a minor discrepancy but a material factual error. The doc's claim contradicts the empirical data it cites. Fix requires: (1) Update line 143 to state "Eight combinations clear 0/10 moderate pairs" (or clarify it as a complete count), OR (2) Change the statement to explicitly qualify it as "Six of the eight combinations that clear 0/10 have complete rank-stability data" and add a note explaining the two additional 0/10 combinations lack Spearman ρ measurements. The suggested fix in the finding (option 1 + explanatory note) is appropriate. The distinction between "all 8 combinations achieve 0/10" vs "6 have computed Spearman data" should be made explicit to avoid reader confusion about whether the results table is complete or filtered.

### [major] Aggregator-sensitivity claim contradicted by its own data  (confirmed)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:53-54, bench/score_redesign_results.md:273-281
- **claim**: The empirics doc claims 'under every tested aggregator, depth is the axis least correlated with overall.' The aggregator-sensitivity table shows depth has the lowest |r| with overall for only 2/7 aggregators (mpi and penalty_geomean). For 5/7 aggregators, equality has lower |r|.
- **evidence**: bench/score_redesign_results.md lines 273-281 shows: geomean (equ=0.069 < dep=0.135), arith (equ=0.074 < dep=0.086), min (equ=0.077 < dep=0.158), harmonic (equ=0.033 < dep=0.187), pgm (equ=0.048 < dep=0.176), penalty_geomean (dep=0.079 < equ=0.144 — only exception), mpi (dep=0.096 < equ=0.129 — second exception).
- **impact**: This weakens a key claim in the empirics: that depth is structurally disconnected from the score. The data actually shows equality is the least-leveraged axis in most aggregators, and depth is mid-pack. The claim is used to justify 'the OECD breach is real but cosmetic in practice,' but if equality is even less influential than depth, the argument inverts — why focus on depth coupling at all?
- **fix**: Correct the claim to: 'Equality is the axis least correlated with overall for 5/7 aggregators; depth is lowest for the two non-compensatory variants (MPI, penalty-geomean). This suggests the depth-coupling issue is a subset concern, not universal.' Revise the 'operational gaming surface' argument accordingly.
- **notes**: This is a logical contradiction at a critical juncture in the design rationale. The claim "under *every* tested aggregator, `depth` is the axis least correlated with `overall`" (SCORE_SHAPE_REDESIGN_EMPIRICS.md:51-52) is demonstrably false: only 2/7 aggregators (mpi, penalty_geomean) show this pattern. For 5/7 aggregators (geomean, arith, min, harmonic, pgm), equality is the axis least correlated with overall. This weakens the "operational gaming surface" argument that depends on depth's low leverage being structurally benign rather than a design choice. The ticket should capture: (1) correction of the universal claim to a conditional one (depth lowest only for non-compensatory variants), (2) implications for the design narrative around whether "depth coupling is cosmetic" is true universally or only for some aggregators, (3) potential need to revise the soft recommendation language around which aggregator to adopt, since the rationale for preferring non-compensatory variants would be stronger if depth is actually a low-leverage axis there.

### [major] Missing spearman data for 4 combos creates incomplete decision record  (confirmed)
- **loc**: bench/score_redesign.py:702-706 (loop over interesting_combos), bench/score_redesign_results.md:528-539 (rank-stability section)
- **claim**: The rank-stability section is the load-bearing decision point (doc line 156: 'The Spearman rank-stability column is the load-bearing one'). Yet 4 out of 8 combinations meeting the 0/10 gate are absent from this section. Their correlations are reported (in cross-product, lines 243-265), but their rank-stability values are never computed, making it impossible to assess whether they fail the actionability gate at the level of actual project re-ranking.
- **evidence**: The script loops only over hardcoded interesting_combos (9 entries). The missing combos are: modular_tangle+depth_size_relative (0/10, r(acy,dep)=-0.102, r(mod,dep)=+0.400), feedback_x_tangle+depth_size_relative (0/10, r(acy,dep)=-0.153), log_cycle_count+depth_size_relative (0/10, r(acy,dep)=-0.173), sentrux_legacy+depth_size_relative (0/10, r(acy,dep)=-0.185). None appear in rank-stability output (lines 528-539).
- **impact**: Without Spearman ρ for these combos, the empirics are incomplete. Specifically: the doc concludes 'all 0/10 combos have substantial leaderboard re-ranking.' But this conclusion covers only 4/8 of the 0/10 combos. The reader cannot verify whether the other 4 also re-rank badly (supporting the conclusion) or unexpectedly preserve ordering (contradicting it).
- **fix**: Extend bench/score_redesign.py to compute and report spearman(baseline_overall, candidate_overall) for all 8 combinations with 0/10 moderate pairs, not just 9 hand-selected combos. Update the table in score_redesign_results.md to include all 8.
- **notes**: The issue has two layers:

1. **Data completeness issue (confirmed)**: The rank-stability section reports spearman ρ for only 4 of the 8 combinations with 0/10 moderate pairs. The missing 4 are all +depth_size_relative variants. This makes it impossible to verify the doc's central claim that "every 0/10 combination re-ranks substantially."

2. **Doc inconsistency (secondary)**: SCORE_SHAPE_REDESIGN_EMPIRICS.md claims "Only six combinations clear 0/10 moderate pairs" (line 143) but the cross-product table in score_redesign_results.md shows 8. The doc's table (lines 147-154) is incomplete—it omits feedback_x_tangle+depth_size_relative and sentrux_legacy+depth_size_relative entirely, and marks modular_tangle+depth_size_relative and log_cycle_count+depth_size_relative as "n/a" rather than showing their actual spearman values.

The suggested fix is correct: extend bench/score_redesign.py to compute spearman for all 8 combinations with 0/10 moderate pairs, not just the hardcoded 9 in interesting_combos. This would require adding at least 4 rows to the rank-stability output (or more if the script's selection criteria should also be clarified).

### [minor] Rank-stability section reports 5 non-0/10 combos but excludes 4 matching-threshold combos  (confirmed)
- **loc**: bench/score_redesign.py:692-701, bench/score_redesign_results.md:528-539
- **claim**: The rank-stability table includes baseline_tangle+depth_baseline (3/10 pairs), baseline_tangle+depth_with_scc_penalty (1/10), baseline_tangle+depth_size_relative (1/10), feedback_edges+depth_baseline (2/10), modular_tangle+depth_baseline (2/10) — none of which meet the 0/10 gate. Meanwhile, it omits modular_tangle+depth_size_relative, feedback_x_tangle+depth_size_relative, log_cycle_count+depth_size_relative, sentrux_legacy+depth_size_relative — all 0/10. This is gate-shifting, not filtering.
- **evidence**: interesting_combos list (lines 692-701) is hardcoded. Cross-product table (lines 243-265) shows which combos have 0/10. Comparing: reported combos include 5 that don't meet 0/10; 4 that do meet 0/10 are missing.
- **impact**: The decision to reject redesigns rests on 'all 0/10 combos fail the rank-stability bar (ρ < 0.9 or substantial re-ranking).' But the evidence supporting this conclusion is incomplete: the worst-performing acyclicity+depth pairs are never tested for rank-stability at all. If they were, they might show even lower ρ (reinforcing the conclusion) or unexpectedly high ρ (weakening it). The bench theater here is: 'we tested everything' (cross-product table shows 21 combos) vs. 'here's the decision' (rank-stability shows 9, with gate criteria selectively applied).
- **fix**: Compute and report Spearman ρ for all 8 combinations with 0/10 pairs. If any show ρ >= 0.9, update the conclusion. If all show ρ < 0.9, strengthen the conclusion statement with the complete evidence set.
- **notes**: The finding is technically correct but overstated. (1) INCOMPLETE EVIDENCE: The conclusion "all 0/10 combos shake the leaderboard substantially" tests only 4 of 8 0/10 combos. The missing 4 all involve depth_size_relative with less-tested acyclicity variants (log_cycle_count, sentrux_legacy, and 2 more with modular_tangle and feedback_x_tangle). However, based on the depth_size_relative pattern in the cross-product table (which shows 0.62-0.68 range for those axes), extrapolation suggests the untested combos would also show ρ < 0.9, so the conclusion is likely still valid even with missing evidence. (2) SELECTIVE FRAMING: The finding misleadingly says the reported table includes "none [that] meet the 0/10 gate" when actually 4 reported combos (feedback_edges+depth_with_scc_penalty, modular_tangle+depth_with_scc_penalty, feedback_x_tangle+depth_with_scc_penalty, feedback_edges+depth_size_relative) have 0/10. This is selective reporting of a hardcoded 9-combo subset, not "gate-shifting" per se. (3) LOW IMPACT: The decision to reject the redesign has already been made; the missing rank-stability values are not actionable. (4) ACTION: If pursuing completeness, compute Spearman ρ for the 4 missing 0/10 combos. All 4 tested 0/10 combos currently show ρ in [0.534, 0.648], supporting the conclusion that 0/10 combos destabilize rankings.

### [minor] Spearman ρ >= 0.9 threshold is asserted but not justified  (confirmed)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:268-271, bench/score_redesign_results.md:686-688
- **claim**: The empirics use ρ >= 0.9 as the gate for 'acceptable rank stability' (changes that preserve rank ordering 'visibly'). This threshold is stated in the rank-stability section header but never justified. The doc's OECD gate (line 271) mentions 'rank stability >= 0.9' but provides no citation or calibration against prior archy version bumps.
- **evidence**: bench/score_redesign.py line 687 comment: 'rho < 0.9 means the leaderboard would visibly shake up.' No explanation for why 0.9, not 0.85 or 0.95. Line 160-162 (empirics doc) compares v0.20 and v0.23 version bumps but doesn't state their Spearman ρ values.
- **impact**: The 0.9 threshold is the gate that rejects all axis redesigns. If the threshold were 0.85, some redesigns might pass (e.g., modular_tangle+depth_with_scc_penalty at ρ=0.638 would still fail, but the gap to passing is larger). Without justification, the gate appears arbitrary. Changing 0.9 to 0.85 would change which redesigns are 'viable' vs. 'broken.'
- **fix**: Compute and report the Spearman ρ values for v0.20 → v0.23 version bumps (if history is available) to calibrate the threshold. If historical ρ values are unavailable, state that 0.9 is an OECD handbook default (cite Section X.Y) or a design choice, not an empirical finding. Add: 'The 0.9 threshold is calibrated against [historical/OECD/design] standards; redesigns with ρ < 0.9 are rejected as introducing unacceptable discontinuity.'
- **notes**: The ticket should require: (1) Either compute historical Spearman rho values for v0.20 to v0.23 version bumps if that data exists in git history or archived baselines, OR state explicitly that no historical version-pair data is available; (2) Cite the specific OECD Handbook section (if any) that recommends rho >= 0.9 for rank stability in composite indicators, or state that 0.9 is an archy design choice; (3) Add explicit language to SCORE_SHAPE_REDESIGN_EMPIRICS.md (line 270-271) clarifying whether 0.9 is calibrated against historical precedent, OECD guidance, or design intent; (4) In the methodology section (around line 264-271), add a paragraph explaining: The 0.9 rank-stability threshold is applied to aggregator changes as a gate for acceptable discontinuity. This threshold is [CALIBRATION BASIS: historical / OECD handbook section X.Y / design choice]. Aggregator redesigns with rho less than 0.9 are rejected as introducing unacceptable leaderboard churn relative to prior version transitions. The 0.9 value directly gates the redesign space (penalty_geomean rho=0.890 fails, pgm rho=0.907 passes), so its justification is load-bearing for design decisions.

### [minor] No comparison of best redesigns against unmodified status quo  (confirmed)
- **loc**: bench/score_redesign_results.md:529-539 (rank stability table), bench/score_redesign.py:689 (baseline_overall)
- **claim**: The rank-stability section compares each redesign's overall ranking to v0.23 baseline (baseline_tangle + geomean). But it never reports the correlation/Spearman ρ within redesign variants themselves. For example: are modular_tangle and feedback_edges+depth_with_scc_penalty more or less correlated with each other than either is with v0.23? This would show whether the redesigns are 'steps in one direction' or 'divergent alternatives.'
- **evidence**: Lines 689-706 compute baseline_overall once (v0.23), then compare each candidate to it. No comparison of candidate_A vs. candidate_B (e.g., feedback_edges vs. modular_tangle at same depth). The cross-product table shows correlations between axes, not between candidate overall scores.
- **impact**: Without inter-redesign correlations, the reader cannot assess whether the redesigns form a coherent family (all pointing the same direction) or scatter broadly. This affects actionability: if two 0/10 redesigns have ρ = 0.5 with each other, they're incompatible; if ρ = 0.95, they're effectively the same. The empirics don't provide this lens.
- **fix**: Add a 'Correlation between best redesigns' section computing Spearman ρ of overall scores between all pairs of top candidates (e.g., feedback_edges+scc_penalty vs. modular_tangle+scc_penalty). Show whether the redesigns converge on a single better-ordered ranking or diverge.
- **notes**: This is a genuine gap in analysis scope, not a bug. The empirics correctly report what they claim to report. However, the suggested addition (Spearman ρ between pairs of top redesign candidates) is a legitimate analytical extension that would add value. The ticket should note:

1. Location: bench/score_redesign.py, evaluate() function, the "Rank stability of winning axis combinations" section (lines 683-708) - currently only does one-vs-baseline comparison

2. Scope of missing analysis: Would need to compute Spearman ρ between all pairs of the 9 candidates in interesting_combos, likely in a new section after "Rank stability of winning axis combinations"

3. Potential candidates to compare (e.g., variants at same depth):
   - feedback_edges + depth_baseline vs modular_tangle + depth_baseline  
   - feedback_edges + depth_with_scc_penalty vs modular_tangle + depth_with_scc_penalty
   - etc.

4. This is genuinely useful: high inter-redesign ρ (>0.9) would indicate they're essentially the same solution; low ρ (<0.5) would indicate they're genuinely alternative approaches. Matters for the "convergent family vs divergent scatter" question the finding raises.

## bench / simulate_oracle-bench


### [minor] Cycle.edges field not compared in oracle  (confirmed)
- **loc**: bench/simulate_oracle.py:87-91, _cycle_keys function
- **claim**: The oracle compares only the modules in cycles, not the edges within cycles. A bug that produces a cycle with the correct module set but different CycleEdge entries (different lines metadata) would pass undetected.
- **evidence**: _cycle_keys returns only (frozenset(frozenset(c.modules) for c in cd.added), frozenset(frozenset(c.modules) for c in cd.resolved)), completely ignoring c.edges which contains CycleEdge objects with source, target, and lines fields. The Cycle class (src/archy/cycles.py:17-21) defines both modules and edges, but only modules are compared.
- **impact**: A regression in how cycle edges are computed or reported (e.g., if edge attribution in cycles becomes incorrect) would not be caught by the oracle, even on clean samples. The oracle reports 0 bugs with 100% pass rate, but this may mask edge-level bugs.
- **fix**: Expand _cycle_keys to also compare cycle edges: compare the full set of (source, target) pairs from c.edges, not just the module set. This will catch bugs where cycles have correct module membership but incorrect internal structure.
- **notes**: This is a real gap in the oracle's validation, but with limited practical impact. The finding correctly identifies that _cycle_keys ignores c.edges. However: (1) The implementation is consistent with the design intent stated in simulate.py lines 14-18 and SPEC_SIMULATE.md lines 82-97, which explicitly document that "cycle identity is frozenset(modules)"; (2) A bug would have to specifically affect cycle-edge computation (not general edge computation), because SCC detection is deterministic - if the same modules form an SCC in both graphs, the internal edges should be the same; (3) The synthetic edge with lines=() affects only line metadata, not edge existence/direction. That said, to align the oracle with the spec's stated comparison "assert sim.cycles == real.cycles" (SPEC line 211) and to catch any edge-level bugs, the oracle should be expanded to also compare the (source, target) pairs from c.edges, as suggested. This would be a defensive improvement, not a critical bug fix. Ticket should specify: (a) expand _cycle_keys to compare edge pairs (source, target), optionally including line numbers for maximum coverage; (b) update the design-intent comment in simulate.py if this becomes more comprehensive; (c) confirm this matches the spec's intent at line 211.

### [minor] SdpViolation instability values not compared  (confirmed)
- **loc**: bench/simulate_oracle.py:101-105, _sdp_keys function
- **claim**: The oracle only compares (source, target) pairs for SDP violations but ignores source_instability and target_instability values. A bug in how simulate computes instability when edges are added would pass silently.
- **evidence**: _sdp_keys returns only frozenset((v.source, v.target) for v in sd.added/resolved), completely excluding v.source_instability and v.target_instability. These are critical fields that get computed in find_sdp_violations (src/archy/layers.py:218-222) using compute_instability(). When an edge is added, node instability values change (I = Ce/(Ce+Ca), where Ce and Ca change with new edges), which can alter which violations are reported and at what magnitude.
- **impact**: If simulate incorrectly computes instability for the hypothetical graph, or if there's an off-by-one error in instability calculation, the oracle would still report 100% pass rate because it only checks the source/target edges, not the computed instability values. The bench results show 0 bug failures, but instability bugs could hide.
- **fix**: Expand _sdp_keys to compare instability values: include v.source_instability and v.target_instability in the comparison with a reasonable epsilon for floating-point equality (similar to the propagation_cost check on line 123-124).
- **notes**: This is not a blocker-level bug but a legitimate coverage gap and defensive programming improvement: (1) **Current scope**: SDP violations are never actually tested in the bench oracle because the corpus carries no archy.yaml, so both simulate and diff always report empty sdp_violations tuples. No bug will hide here today. (2) **Future-proofing**: If SDP becomes enabled in real-repo benchmark scenarios, expand _sdp_keys to include instability values with epsilon comparison (e.g., 1e-12 tolerance) for floating-point equality, matching the pattern on lines 123-124 for propagation_cost. (3) **Consistency**: The unit test (test_simulate.py:181) correctly uses full object comparison `assert sim.sdp_violations == real.sdp_violations`, so the weakness is isolated to the bench's _sdp_keys function, not the code under test. (4) **Related coverage**: The bench's _violation_smoke only tests forbid rules, not SDP rules - add SDP configuration to that smoke test if SDP testing is prioritized. Document in the Gaps section that SDP violations lack real-repo oracle coverage due to missing archy.yaml configs in the corpus.

### [minor] Misleading comment in simulate.py about field safety  (confirmed)
- **loc**: src/archy/simulate.py:14-18
- **claim**: The comment states that synthetic edges with lines=() are safe because 'none of those read lines', but this conflates 'not needed for graph structure' with 'not part of the data model'. The oracle only checks a subset of fields, not all fields that exist in the data model.
- **evidence**: The comment says 'the simulated and real graphs agree on every field the report compares', which is tautological and misleading. It should clarify that only specific fields (module sets, rule identities) are used for structural computation, not that all fields match. The Cycle, Violation, and SdpViolation objects all have lines fields that WILL differ between simulated and real graphs, and the oracle currently doesn't check them.
- **impact**: Future developers might assume the oracle validates all fields and not realize that lines metadata and instability values are unchecked. This could lead to latent bugs not being caught.
- **fix**: Clarify the comment to explicitly state which fields are used for structural identity (modules, rule identity, source/target pairs) and note that lines and instability are not currently validated by the oracle. Add a TODO or FIXME noting the gap.
- **notes**: The finding is factually correct but the impact is LIMITED to documentation clarity rather than a functional bug. The lines metadata difference does not affect structural predictions because: (1) The oracle validates only the structural identities (cycles' module sets, violations' rule+endpoints, SDP violations' source+target pairs), which match perfectly (315/315 in bench). (2) Score deltas, cycle presence, violation presence, and back-edge predictions are all correct despite lines differences. (3) Lines are informational metadata telling where imports exist in code, not data used in structural computation. The comment in simulate.py:14-18 should be improved to explicitly state: "Cycle identity uses frozenset(modules); violation identity uses (from_layer, to_layer, source, target); SDP violation identity uses (source, target). None of these identity functions use the lines field. The lines metadata will differ between simulated (empty) and real (populated) graphs but do not affect structural predictions because lines are not inputs to cycle detection, violation matching, or score computation. The oracle validates structural identity, not metadata completeness." Additionally, a note could be added that instability values in SdpViolation are also not currently validated by the oracle. This is a documentation gap, not a logic bug in the oracle itself.

### [minor] No validation of SDP violation detection itself when edges are added  (confirmed)
- **loc**: bench/simulate_oracle.py:199-261, evaluate function and corpus setup
- **claim**: The corpus contains no archy.yaml files with layer rules, and SDP checking is off by default (sdp.enabled=False). This means SDP violations are never actually tested on real repos, only on synthetic 400-node graphs in _violation_smoke. The oracle could pass 100% even if simulate has bugs in SDP violation detection.
- **evidence**: The docs at the end of simulate_oracle_results.md state: 'Violation prediction reuses archy's own find_violations on the hypothetical graph; covered by the synthetic smoke above and unit tests, not by real-repo layer rules (the corpus carries no archy.yaml).' The _violation_smoke function (line 327-354) creates a synthetic graph with specific layers and only checks that forbidden edges are flagged and allowed edges are silent. It does not check instability computation or any interaction with real SDP violations.
- **impact**: Any bug in SDP violation handling (instability computation, edge-attribution, filtering logic) could exist without being caught by the real-corpus tests. The oracle reports 0 bugs, but this only validates the 4 real repos where SDP is disabled. The _violation_smoke is a thin smoke test that does not exercise the full SDP machinery.
- **fix**: Either (1) enable SDP checking in the synthetic test to validate instability computation end-to-end, or (2) add a synthetic corpus that exercises SDP violations and verifies instability values match between simulate and real diff. The current setup tests only boolean presence/absence of violations, not their properties.
- **notes**: The gap is documented in the codebase's own results file. To fix: (1) add an archy.yaml with sdp: {enabled: true} to _violation_smoke, (2) verify instability computation in both simulate and real diff paths match on test graphs with actual SDP violations (not just presence/absence). This is a bench enhancement, not a correctness bug in simulate.py itself. The oracle still validates the other 7 dimensions comprehensively on 4 real repos with 327 samples (315 clean, 0 mismatches). SDP is out-of-scope for the current oracle but acknowledged as a gap.

## bench / typehint-bench


### [major] Bimodal distribution treated as continuous variable confounds correlation analysis  (confirmed)
- **loc**: bench/typehint_coverage.py (lines 277-279), bench/typehint_coverage_results.md (lines 41-52)
- **claim**: The benchmark computes Pearson correlations between type-hint coverage and archy axes over all 27 projects, but the distribution is sharply bimodal (14 at >0.85, 9 at <0.50), reflecting a categorical choice (project generation/typing policy adoption) rather than a continuous variable. Pearson r on stratified/categorical data is misleading.
- **evidence**: From results.md: 14 projects in high tier (>0.85) are all post-2015 modern libraries; 9 in low tier (<0.50) are legacy, generated, or pre-PEP-484 projects. The bimodal structure is explicitly acknowledged in TYPE_HINT_COVERAGE_EMPIRICS.md as 'temporal distribution as much as a quality distribution,' yet the correlation analysis treats coverage as continuous. Pooled Pearson r=-0.542 (acyclicity) may largely reflect age/generation confounding rather than a real relationship between typing and modularity.
- **impact**: The independence test is compromised. The reported orthogonality (max |r|=0.551) may be artificially inflated by confounding from the binary generation variable. The benchmark cannot claim the metric is independent when the apparent correlation might be driven by which era of projects happened to adopt typing, not by typing itself.
- **fix**: Stratify the analysis by project generation (pre-2015 vs post-2015) or by explicit typing-policy choice. Report within-group correlations alongside pooled correlations. If within-group r is substantially smaller, document this as evidence of confounding. Alternatively, explicitly frame the metric as 'generational marker' rather than 'pure typing coverage metric.'
- **notes**: 1. The confounding is real and quantifiable: stratified correlations show sign reversals for modularity (high r=-0.358, low r=+0.512) and depth (high r=+0.450, low r=-0.322), indicating the pooled correlations are not monotonic across the continuous coverage spectrum.

2. The TYPE_HINT_COVERAGE_EMPIRICS.md document identifies the problem (lines 55-60) but does not quantify it. The document should be updated to include stratified correlation tables showing within-group vs. pooled correlations if it claims to have evaluated independence.

3. The bench script (typehint_coverage.py lines 277-279) should optionally support stratified correlation reporting. If the metric were ever reconsidered for promotion, this analysis would be essential.

4. The "don't ship" decision is not directly justified by the confounding analysis; instead, it's a value-prop argument (lines 99-120) that type-hint coverage doesn't deepen archy's niche. The confounding is acknowledged but treated as a secondary concern.

5. For future reference: if stratified analysis shows substantially weaker within-group correlations, the metric should be explicitly framed as a "generational/adoption marker" rather than a "pure typing policy metric."

### [minor] Sample size (N=27) insufficient to claim borderline correlation (r=0.551) is 'weak'  (confirmed)
- **loc**: bench/typehint_coverage_results.md (lines 47-49), docs/research/TYPE_HINT_COVERAGE_EMPIRICS.md (line 46)
- **claim**: The benchmark reports r=-0.542 (acyclicity) and r=+0.551 (depth) as evidence of 'weak independence' and labels 0.551 as the 'weakest archy has measured.' With N=27, the 95% CI for r=0.551 is [+0.216, +0.770], spanning from weak to strong correlation. The point estimate is not a reliable summary.
- **evidence**: 95% CI for r=0.551 with N=27 is approximately [+0.216, +0.770] (using Fisher z-transform). The upper bound exceeds common medium-effect thresholds. The standard error on the estimate is substantial. The same applies to r=-0.542 CI approximately [-0.765, -0.204]. These ranges were not computed or reported in either document.
- **impact**: The conclusion 'independence is weak' is stated as fact despite substantial statistical uncertainty. A conservative analysis would flag these estimates as unreliable and recommend a larger sample (N>100) before concluding on the magnitude of the correlation. The independence test does not falsify the hypothesis; it merely fails to prove strong independence with low power.
- **fix**: Report 95% confidence intervals for all five Pearson correlations. Explicitly state that N=27 provides low power to distinguish medium from weak correlations. Either increase sample size to N>100 or reframe the conclusion as 'inconclusive on independence; bounds range from weak to moderate.'
- **notes**: **Valid methodological concern, but limited impact to the actual decision.** The documents should report 95% confidence intervals for the Pearson correlations (currently missing). However: (1) The documents use "borderline" language more consistently than claimed, suggesting awareness of uncertainty; (2) The final decision to not ship type-hint coverage rests primarily on strategic fit and value-prop arguments, not on the independence criterion being definitively weak; (3) This is a reporting/rigor issue rather than a substantive finding reversal—the conclusion would likely remain unchanged even with proper CI reporting, since the strategic arguments are independent of the statistical uncertainty.

If this is being tracked for documentation improvement, the ticket should request: Add 95% confidence intervals using Fisher z-transform for all five Pearson correlations (modularity -0.511, acyclicity -0.542, depth +0.551, equality +0.037, complexity -0.030). Explicitly state that N=27 limits power to distinguish medium from weak correlations. Consider adding a sensitivity note that the decision not to ship type-hint coverage does not rest primarily on the independence criterion being weak, but on strategic focus (archy is a graph-shape sensor) and tooling-niche arguments (mypy/pyright own the typing domain).

### [minor] Public function definition (includes dunders) may not represent actual user-facing API  (confirmed)
- **loc**: bench/typehint_coverage.py (lines 59-68)
- **claim**: The _is_public() function counts dunders (__init__, __call__, __enter__, etc.) as 'public API' and includes them in coverage metrics. Dunders are runtime-invoked, not directly called by users. Including them inflates coverage for projects that type __init__ but leave other methods untyped, making the metric biased toward OOP-heavy, class-based libraries.
- **evidence**: Lines 59-68: returns True for any function starting with __ and ending with __, including __init__, __call__, __enter__, __exit__. These are runtime hooks, not user-facing API. Example: requests.Response.__init__ is not called by users; they call requests.get() which implicitly constructs Response. Typing __init__ signatures does not improve user-facing API documentation.
- **impact**: Coverage metric is measuring 'typing discipline on dunder implementations' rather than 'typing of public user-facing functions.' Projects with well-typed __init__ but sparse typing elsewhere appear better-typed than they actually are. The metric no longer cleanly represents 'public API coverage.'
- **fix**: Consider excluding dunders from the metric, or compute two separate metrics: (1) non-dunder public functions, (2) all public including dunders. Report both and analyze whether exclusion of dunders changes the correlation structure.
- **notes**: This is a design-choice concern, not a bug. The code correctly implements the documented behavior of treating dunders as public API. However, the finding raises a valid point: dunder methods are invoked by Python's runtime (via implicit constructor calls, context managers, etc.) rather than explicitly by end users, and they may be systematically better-typed than regular methods, potentially biasing the metric. The current implementation does not provide metrics separating dunder vs non-dunder coverage. If this is pursued, the ticket should: (1) acknowledge that including dunders in "public API" is a deliberate design choice (not a bug), (2) propose either excluding dunders entirely (with justification for why) or computing two separate metrics (non-dunder public + all public) to assess whether the bias actually affects interpretation, (3) clarify whether the distinction matters for the metric's intended use case (which is currently "not shipping in any form" per TYPE_HINT_COVERAGE_EMPIRICS.md). The finding's suggested action (compute two metrics to analyze correlation impact) is reasonable but would require re-running the empirics.

### [minor] Unweighted vs function-count-weighted means diverge significantly (0.628 vs 0.541)  (confirmed)
- **loc**: bench/typehint_coverage.py (line 248), bench/typehint_coverage_results.md (line 39)
- **claim**: The benchmark reports mean coverage as 0.628 (simple average across 27 projects), but the function-weighted mean is 0.541 (accounting for project size). Large projects (>5000 functions) comprise 56% of the sampled functions but only 4 out of 27 data points. The reported mean is unrepresentative of the actual distribution of code.
- **evidence**: With N=27 unweighted, each project is one data point. Large projects like django (7731 funcs at 0.000), sqlalchemy (7945 funcs at 0.616), mypy (5474 funcs at 0.950), dagster (8831 funcs at 0.814) are each one point. The simple mean (0.628) treats them equally to msgspec (34 functions). The function-weighted mean (0.541) is more representative of the actual Python code distribution.
- **impact**: Correlation analysis is over-weighted toward small libraries, which are more likely to be modern and typed. Larger, legacy codebases are under-weighted in the correlation structure, biasing the orthogonality test toward the 'modern small projects' cluster.
- **fix**: Compute and report correlation analysis using function-count weighting (weighted Pearson). Compare weighted vs unweighted results. If they diverge meaningfully, report both and discuss which is appropriate for the decision.
- **notes**: The finding is technically correct but requires scope clarification: (1) The strategic decision to exclude type-hint coverage (per TYPE_HINT_COVERAGE_EMPIRICS.md and AXIS_REVIEW.md line 178) was already made in 2026-05, independent of weighting concerns. This decision was based on weak independence scores (max |r|=0.551, the weakest archy measured), discriminant-validity issues (django/numpy/boto3 are respected despite zero/near-zero coverage), and a value-prop argument (mypy/pyright already own this niche). (2) However, neither TYPE_HINT_COVERAGE_EMPIRICS.md nor AXIS_REVIEW.md discuss or acknowledge whether function-count weighting would meaningfully change the orthogonality conclusions. This is a gap in the analysis: if weighted Pearson correlations diverge significantly from unweighted ones (as the finding suggests they might), it should be noted even if the final decision remains "don't ship." (3) The finding's suggested action (compute and report weighted vs unweighted correlations, compare results) is reasonable from an analytical rigor perspective, but the absence of this comparison does not invalidate the decision already made. (4) Ticket should capture: (a) acknowledge the weighting asymmetry (small projects overrepresented relative to code volume), (b) suggest computing function-weighted Pearson alongside unweighted for future axis-promotion studies, (c) clarify whether this reanalysis would change any conclusions about type-hint coverage or other candidates.

### [minor] Comparison to cc_mean (v0.20 precedent) uses different framing without justification  (confirmed)
- **loc**: docs/research/TYPE_HINT_COVERAGE_EMPIRICS.md (lines 44-49)
- **claim**: The analysis compares type-hint coverage to cc_mean (complexity, promoted in v0.20) on the OECD scorecard. cc_mean shows max |r|=0.197 (strong orthogonality), while type-hint shows max |r|=0.551 (borderline). This is presented as a negative for type-hint, but both metrics pass the stated 0.7 OECD redundancy threshold. The comparison conflates 'more orthogonal is better' with 'this metric is orthogonal,' which are different claims.
- **evidence**: Lines 44-49 table shows cc_mean at 0.197 and type-hint at 0.551 under 'Independence.' Both are below 0.7. But the narrative treats 0.551 as 'weak' and 0.197 as 'strong,' implying the lower value is better. If 0.7 is the threshold, both pass, and comparison on 'which is more orthogonal' is a tiebreaker, not a primary gate.
- **impact**: Readers may conclude that type-hint coverage failed the independence criterion, when in fact both metrics satisfy it. The comparison is being used to argue 'cc_mean was more orthogonal so it was worth promoting; type-hint is less orthogonal so it should be rejected.' But this ignores that cc_mean also faced other trade-offs (e.g., direction depends on domain/intent, like calls_per_edge) and was promoted anyway.
- **fix**: Clarify that both metrics pass the 0.7 redundancy threshold and that 'more orthogonal is not automatically better.' Use explicit ranking: 'This metric is [N]th most orthogonal among all candidates tested,' not implicit comparison. Separate the orthogonality assessment from the strategic scope assessment.
- **notes**: The comparative framing issue is real but contained:

1. **What should be clarified**: The OECD Independence section (lines 44-60) uses comparative language ("weakest", "borderline") that treats independence as a ranking, but the 0.7 threshold is actually binary. A reader could mistakenly think orthogonality drives the rejection when it doesn't.

2. **Actual decision drivers**: The document correctly routes the rejection through (a) Discriminant Validity failure (django/numpy/boto3), and (b) Value-prop argument (existing tools, not structural, slippery slope). The Independence comparison is context but not a deciding factor.

3. **Suggested improvement**: 
   - Make explicit in the Independence subsection that both metrics pass the 0.7 threshold (currently implied but not stated)
   - Clarify that the comparative assessment (0.551 vs 0.197) is about degree-of-orthogonality, not about pass/fail status
   - Separate the "how orthogonal is each metric" question from the "should we ship it" question more cleanly

4. **Why it matters**: archy is a reference document for future axis candidates. The OECD framework framing should be precise so future decisions can apply the same criteria consistently.

5. **Not a major bug**: The document does eventually make the actual decision criteria clear (through discriminant validity and value-prop arguments), so readers willing to follow the full argument will not be misled. The issue is early-stage presentation clarity.

### [nit] No significance testing or multiple-comparison correction despite five simultaneous correlation tests  (confirmed)
- **loc**: bench/typehint_coverage.py (lines 277-279), bench/typehint_coverage_results.md (lines 45-51)
- **claim**: The script computes five Pearson correlations (vs modularity, acyclicity, depth, equality, complexity) but reports no p-values, no multiple-comparison correction (Bonferroni), and no confidence intervals. With N=27 and uncorrected testing, the false-discovery rate is elevated.
- **evidence**: Lines 277-279 compute and print five correlations with no significance testing. At α=0.05 uncorrected, expect ~0.25 false positives across five tests. With N=27 and moderate effect sizes, power is low; reported values lack statistical inference support.
- **impact**: Readers cannot assess whether reported correlations are statistically distinguishable from noise. The reported max |r|=0.551 may or may not be significant; the benchmark does not state this. Three of five correlations sit at moderate strength (0.5-0.7); without significance testing, it is unclear whether these represent real structure or sampling variability.
- **fix**: Compute p-values for each correlation (Pearson t-test, two-tailed). Apply Bonferroni correction (α/5). Report p-values and 95% CIs alongside point estimates. Flag which correlations survive correction.
- **notes**: This finding conflates exploratory empirical analysis with published inference. The script IS exploratory axis-candidate evaluation where reporting point estimates + distribution is appropriate. The developers explicitly considered statistical independence in their OECD framework analysis and rejected type-hint coverage on substance (existing tooling adequacy, architectural niche mismatch, discriminant validity) rather than on statistical significance grounds. Ticket scope: If improving exploratory bench reporting is desired, add p-values and Bonferroni-corrected significance flags as contextual information, but acknowledge this was an intentional design choice for exploratory work. Reference: bench/typehint_coverage.py lines 17-24 (docstring stating "evaluate orthogonality criterion"), docs/research/TYPE_HINT_COVERAGE_EMPIRICS.md lines 40-49 (OECD check), and docs/research/AXIS_REVIEW.md (full axis-promotion framework) which shows the developers were already aware of the independence weakness and made their decision on other grounds.

## code / cli


### [major] affected command does not validate depth >= 1 at CLI layer  (confirmed)
- **loc**: src/archy/cli.py:439-467 (affected function)
- **claim**: The affected command accepts --depth values < 1 (including negative and zero) without validation at the CLI layer. The error only surfaced when find_affected() is called internally.
- **evidence**: Running `archy affected . pkg/a.py --depth -1` or `--depth 0` causes an unhandled ValueError from find_affected() rather than a user-facing CLI error. The MCP layer validates this at src/archy/mcp.py:1179-1180 with `if depth < 0: raise ValueError(...)`, but affected requires depth >= 1 per src/archy/affected.py:77.
- **impact**: Users see a raw Python exception instead of a helpful error message. The CLI should catch and convert this to a click.ClickException or click.UsageError for consistency with other commands (check, what-to-refactor-next).
- **fix**: Add validation in the affected() function before calling find_affected: `if depth < 1: raise click.ClickException(f'--depth must be >= 1; got {depth}')`
- **notes**: The affected command should validate `depth >= 1` in its CLI function before calling find_affected(). The validation should raise click.ClickException with message like `"--depth must be >= 1; got {depth}"` to match the pattern used in what-to-refactor-next command (cli.py:572-573). The Click decorator could also use `click.IntRange(min=1)` on the --depth option for even better validation at the Click framework level. Note that MCP layer uses >= 0 for graph_focus (which allows radius=0), but affected requires >= 1 per its business logic.

### [major] cycles --min-size accepts invalid values (zero, negative) silently  (confirmed)
- **loc**: src/archy/cli.py:122-134 (cycles function)
- **claim**: The cycles command accepts --min-size values <= 1 without validation. The docstring states min_size is 'Minimum SCC size to report' with default 2, but accepts 0, -1, etc. silently and returns 'No cycles found' for any negative min_size.
- **evidence**: Running `archy cycles . --min-size 0` returns 'No cycles found (min_size=0)' without error. Running with -1, -5, -100 produces similarly silent behavior. The find_cycles() logic at cycles.py:line ~30 checks `if size < min_size: continue`, so negative values create impossible conditions.
- **impact**: Silent wrong answers: users may believe they have no cycles when in fact the filter is invalid. A project with cycles could pass a malformed `--min-size -1` gate.
- **fix**: Add validation in cycles(): `if min_size < 1: raise click.ClickException(f'--min-size must be >= 1; got {min_size}')`
- **notes**: The finding is accurate. Critical points for the ticket: (1) The cycles command's --min-size option accepts any integer via Click's int type without range validation; (2) Negative and zero min_size values are invalid per the docstring ("Minimum SCC size to report" implies >= 1); (3) Negative values make the filter ineffective (size < -1 is never true for real components), causing all cycles to pass through (not be filtered); (4) The behavior is silent - no error raised, just misleading "No cycles found" message; (5) Suggested fix in finding (validate in cycles() function) is appropriate. Implementation location: /Users/hosanglee/archy/src/archy/cli.py function cycles() at lines 122-134. Test location: /Users/hosanglee/archy/tests/test_cli.py should add test for --min-size values <= 0.

### [major] Inconsistent error handling for invalid arguments: ValueError vs ClickException  (confirmed)
- **loc**: src/archy/cli.py (multiple commands)
- **claim**: The CLI uses inconsistent error handling. Some commands (what-to-refactor-next) validate at the CLI layer and raise click.ClickException; others (affected) let ValueError bubble up uncaught. This creates inconsistent UX.
- **evidence**: what-to-refactor-next at line 572-575 explicitly validates and raises click.ClickException. affected at line 467 calls find_affected() which raises ValueError, caught by Click and shown as a raw traceback. compare to check command at line 165 which raises click.ClickException for config errors.
- **impact**: Inconsistent error messages and exit codes. Users see raw tracebacks for some argument errors but polished error messages for others. This violates the CLI design principle of user-facing errors.
- **fix**: Standardize: all CLI argument validation should happen at the CLI layer using click.ClickException or click.UsageError. Remove reliance on lower-layer ValueError for input validation.
- **notes**: The ticket should capture that this is a broader pattern affecting multiple commands (not just `affected`), including:

1. `affected` command: `--depth` validation missing (raises ValueError in find_affected, line 77)
2. `affected` command: `--filter` validation missing (invalid regex raises re.error in _compile_glob, line 144)
3. `hotspots` command: `--top` validation missing
4. Other commands that call functions raising exceptions without wrapping

The proper fix should be: validate argument constraints at the CLI layer BEFORE calling functions, and always raise click.ClickException (or click.UsageError) for user-facing input errors. This matches the pattern already established in `check` command (lines 165-183) and `what-to-refactor-next` (lines 572-575).

Suggested standardization: add explicit validation for all integer option/argument parameters that have semantic constraints before calling downstream functions. This ensures consistent user-facing error messages and exit codes across all commands.

### [minor] impact --max-chains does not validate semantic range (0 accepted)  (confirmed)
- **loc**: src/archy/cli.py:358-384 (impact function)
- **claim**: The impact command's --max-chains accepts 0 without error, but the help text says 'Use a negative value for all' (implying 0 is undefined). --max-chains 0 produces valid JSON with chains_omitted > 0 but chains=[],  which is semantically unclear.
- **evidence**: Running `archy impact . --file pkg/a.py --max-chains 0 --format json` returns chains=[] and chains_omitted=1. The docstring at impact.py says 'capped at `max_chains` (set negative for all)', making 0 an undocumented edge case. The MCP layer has no explicit validation either.
- **impact**: Ambiguous behavior: users cannot distinguish between 'no chains' and 'chains omitted due to cap of 0'. The documented interface only mentions negative (all) or positive integers, making 0 undefined.
- **fix**: Add validation in impact(): `if max_chains == 0: raise click.ClickException('--max-chains must be negative (for all) or positive (for a limit); got 0')`
- **notes**: This is an undocumented edge case in the --max-chains parameter. The documented interface clearly says "negative for all" and implicitly means positive integers for a limit, but 0 is accepted by Click without validation. The semantic issue is real: when max_chains=0, chains=[] and chains_omitted=N, making it impossible for users to know if there were genuinely zero dependencies or if all were filtered. The suggested fix (add validation to reject 0 with a clear error message in the impact() function at cli.py:366-384) is appropriate. No urgent user impact since max_chains=0 is rarely used in practice, but it violates the documented API contract and should be rejected early with a helpful error message rather than silently returning ambiguous output.

### [minor] trend --last accepts zero and negative without validation  (confirmed)
- **loc**: src/archy/cli.py:305-334 (trend function)
- **claim**: The trend command accepts --last values <= 0 without validation. The help text says 'Number of most-recent records to display' but --last -1, 0 are silently accepted.
- **evidence**: Running `archy trend . --last -1` and `archy trend . --last 0` both return 'No archy score history' rather than an error. The code at line 309 uses `window = rows[-last_n:] if last_n > 0 else rows`, which silently accepts zero as 'show all'.
- **impact**: Confusing semantics: --last 0 shows all rows (undefined behavior per help text), --last -1 shows nothing visibly different from 0. No validation prevents accidental misuse.
- **fix**: Add validation in trend(): `if last_n < 1: raise click.ClickException(f'--last must be >= 1; got {last_n}')`
- **notes**: The ticket should note: (1) Both JSON and text output paths have identical undefined behavior (both use the same conditional), (2) the help text explicitly promises "Number of most-recent records" but zero/negative values show all records, (3) no Click-level validation exists, (4) the project uses explicit validation in similar commands (e.g., what_to_refactor_next validates --top >= 1), (5) suggested fix is sound: add `if last_n < 1: raise click.ClickException(f'--last must be >= 1; got {last_n}')` at line 306 or add a Click callback validator to the option decorator. Minor severity is appropriate because the practical impact is low (users are unlikely to pass negative values), but the confusing semantics merit fixing to match documented behavior and project patterns.

### [minor] dsm command does not validate negative focus_depth or max_nodes  (confirmed)
- **loc**: src/archy/cli.py:829-888 (dsm function)
- **claim**: The dsm command accepts --focus-depth and --max-nodes values < 0 without validation. No bounds checking occurs at the CLI layer.
- **evidence**: Running `archy dsm . --focus=pkg.a --focus-depth -1` renders the DSM with a -1 focus_depth parameter (unclear semantics). Running `archy dsm . --max-nodes -1` produces error message 'DSM: 2 modules exceeds max_nodes=-1' instead of rejecting the argument upfront.
- **impact**: Silent acceptance of invalid arguments: users may unknowingly pass malformed parameters. The error message is confusing ('exceeds -1' is nonsensical).
- **fix**: Add validation in dsm(): `if focus_depth < 0: raise click.ClickException(...)` and `if max_nodes < 1: raise click.ClickException(...)`
- **notes**: 1. SILENT ACCEPTANCE: focus_depth negative values are silently clamped to 0 via `max(depth, 0)` in dsm.py:154, producing only the focus node. The semantic intent ("hop count") is violated without user awareness. 2. CONFUSING ERRORS: max_nodes negative values produce error messages like "exceeds max_nodes=-1" which are nonsensical (numbers cannot exceed -1). 3. ROOT CAUSE: Both parameters are Click options with type=int but no validation callbacks or bounds checking. 4. SUGGEST VALIDATION: (a) if focus_depth < 0: raise click.ClickException("--focus-depth must be >= 0"), (b) if max_nodes < 1: raise click.ClickException("--max-nodes must be >= 1"). Location: src/archy/cli.py:829-839 (dsm function body, after parameter binding). 5. NO TESTS: tests/test_dsm.py contains no tests for negative parameter values.

### [minor] score --strict-tolerance accepts out-of-range values without validation  (confirmed)
- **loc**: src/archy/cli.py:247-281 (score function)
- **claim**: The score command's --strict-tolerance parameter accepts any float, including negative and values > 1, without validation. No bounds checking occurs at the CLI layer even though this is a tolerance threshold.
- **evidence**: Running `archy score . --strict-tolerance -0.5` and `archy score . --strict-tolerance 1.5` both succeed without error. The parameter controls pass/fail logic at line 280: `if gate["delta"] < -strict_tolerance`, so negative tolerance inverts the logic.
- **impact**: Semantic confusion: negative tolerance inverts the strict gate logic (passing when score drops). Values > 1 are nonsensical for a tolerance. Users may accidentally enable unintended behavior.
- **fix**: Add validation in score(): `if not 0.0 <= strict_tolerance <= 1.0: raise click.ClickException(f'--strict-tolerance must be in [0, 1]; got {strict_tolerance}')`
- **notes**: The vulnerability requires explicit user input of invalid values to manifest - it won't occur in normal usage with the default tolerance of 0.02. However, the semantic inversion is real and could be confusing in edge cases. The ticket should note:

1. Line 280 of /Users/hosanglee/archy/src/archy/cli.py needs bounds validation
2. Suggested fix location is correct: validate in the score() function before using strict_tolerance
3. Bounds should be: 0.0 <= strict_tolerance <= 1.0 (matching the [0,1] scale of score components)
4. Test coverage gap: no unit tests exist for out-of-range tolerance values in test_cli.py
5. The helper functions _gate_to_dict (line 1207) and _gate_to_text (line 1219) correctly implement the logic; the problem is purely at the CLI input boundary

### [minor] contracts command exits with code 2 for config errors; other commands use 1  (confirmed)
- **loc**: src/archy/cli.py:759-769 (contracts function)
- **claim**: The contracts command uses `sys.exit(2)` for missing configuration (ContractsNotAvailable, ContractsConfigError) but all other gate-status commands use `sys.exit(1)` for violations. Exit code 2 typically signals argument/usage errors in Unix conventions, not missing config.
- **evidence**: Line 763: `sys.exit(2)` for config errors. Compare to line 206 (check command) and 134 (cycles command) which use `sys.exit(1)` for violations. POSIX convention: exit 2 = misuse of shell command.
- **impact**: Scripts and CI pipelines may misinterpret exit code 2 as a usage error rather than a gate failure. Inconsistent with the rest of archy's CLI.
- **fix**: Change contracts to use `sys.exit(1)` for config errors (consistent with check/cycles) or document the exit code semantics. If 2 is intentional, document it in the docstring.
- **notes**: The finding is confirmed. SCOPE: The ticket should clarify whether exit code 2 is intentional (to distinguish config errors from gate violations) or an oversight. If intentional, document it in the docstring and README's CLI section. If unintentional, align with check/cycles/score by changing to sys.exit(1) or raising click.ClickException instead of catching and calling sys.exit(2). The current behavior is problematic because: (1) It contradicts the docstring claim of flowing through the same channel as other commands, (2) Exit code 2 typically signals usage/argument errors in POSIX convention, not missing dependencies, (3) The check command handles its own config errors with exit code 1. The inconsistency is not a blocker but reduces predictability for CI pipelines.

### [minor] affected command --stdin accepts stdin even when positional files are provided  (confirmed)
- **loc**: src/archy/cli.py:456-463 (affected function)
- **claim**: The affected command allows both positional file arguments and --stdin to be used together, silently combining them. The help text suggests they are alternatives ('Pass files as arguments or use --stdin'), but both are accepted.
- **evidence**: Line 456-458: `file_list = list(files); if from_stdin: file_list.extend(...)`. There is no mutual-exclusion check like the one at line 453 (`if as_json and quiet: raise click.UsageError`).
- **impact**: User confusion: the help text says 'or' (exclusive) but the code allows 'and' (inclusive). A user piping to --stdin while also passing files may think stdin is ignored.
- **fix**: Either (1) add validation `if from_stdin and files: raise click.UsageError(...)` to enforce exclusivity, or (2) update the help text to clarify that both are combined.
- **notes**: The ticket should capture: (1) The actual behavior: both positional files and --stdin are accepted and combined. (2) The documented behavior: help text and error message imply they are mutually exclusive. (3) Lack of test coverage for the combined scenario. (4) Two options for resolution: enforce exclusivity with a validation check (matching the pattern at line 453-454), or update documentation to clarify that both are combined. The suggested action in the finding is reasonable. Consider that allowing both could be intentional (process multiple sources), but if not, the exclusivity check should happen early and should match the existing error message pattern. File location: /Users/hosanglee/archy/src/archy/cli.py, function `affected`, lines 439-475.

## code / diff+diff_summary


### [major] Score-component-drop risk saturation hides relative severity  (confirmed)
- **loc**: src/archy/diff_summary.py:268-272 (_score_risk function)
- **claim**: The magnitude-based risk scaling saturates at |delta| >= 0.20, collapsing all larger regressions to risk=1.0 with no distinction between a 0.21 drop and a 0.40 drop
- **evidence**: The formula `risk = clamp(abs(delta) * 5.0, 0, 1.0)` means any delta >= 0.20 yields risk=1.0. Test: delta=-0.21 and delta=-0.40 both produce risk=1.0. When multiple components regress (e.g., modularity -0.25, acyclicity -0.22), both saturate to risk=1.0 and tie-break alphabetically by component name rather than by magnitude, so the agent cannot distinguish a -0.40 drop from a -0.21 drop.
- **impact**: Agents cannot prioritize multi-component regressions by severity. A catastrophic drop (-0.40) sorts identically to a moderate drop (-0.21) if both involve different components. The top_regressions list becomes opaque on the rank ordering for large deltas.
- **fix**: Either (1) extend the scaling range so larger magnitudes map to higher risk (e.g., use a sigmoid or log curve), (2) use the raw delta value as the risk for score-component items instead of the magnitude scaler, or (3) document this as a known limitation and document that all |delta| >= 0.20 regressions are equally high-priority.
- **notes**: 1. CONFIRMED: Score-component risk saturation is real and is by design (documented as "big regression floor" threshold). The x5 scaler intentionally maps 0.20 drops to risk=1.0.

2. CONFIRMED: The saturation does collapse magnitude information for large regressions. A -0.25 drop and a -0.40 drop both produce risk=1.0.

3. CONFIRMED: Alphabetical tie-breaking on description does lose ordering. When both components saturate to 1.0, "acyclicity" sorts before "modularity" regardless of magnitude difference.

4. SCOPE: The impact affects the top_regressions ordering ONLY when multiple score components regress beyond 0.20 in a single commit. This is likely rare (5 components max) but when it occurs, severity ordering is lost.

5. MITIGATION: The raw delta values ARE preserved in the description field ("dropped -0.400") so human reviewers and prompts still see magnitude. The issue is purely in the RANKING for agent-driven prioritization.

6. DESIGN QUESTION: Is the intentional "big regression floor" correct? The comment cites empirical 27-project bench data, but no data is found in the repo justifying why 0.20 is the right threshold. The OECD handbook cited in score_redesign_literature.md uses 0.5-0.7 as the "awkward middle band" for composite indicators, not 0.20.

7. SUGGESTED FIXES: (a) Extend scaling range using sigmoid/log so larger magnitudes map higher (e.g., risk = min(1.0, 1 + log(abs(delta) * 10))); (b) Break ties by raw magnitude instead of alphabetically (add a secondary sort key `(abs(delta), i.description)`); (c) Document this as a known limitation with rationale for the 0.20 floor.

### [major] Resolved-cycle risk can be zero when any member hits zero on one factor, breaking ranking  (confirmed)
- **loc**: src/archy/diff_summary.py:212 (_collect_improvements) calls _max_module_risk which delegates to risk.compute_edit_risk via the caller's risk dict
- **claim**: The geometric mean in compute_edit_risk (propagation_cost * fan_in_norm * instability)^(1/3) zeros out when any single factor is 0 for the max-risk member of a resolved cycle, producing risk=0.0 even for a large, impactful resolution
- **evidence**: The FUTURE.md explicitly documents this: 'resolved-cycle risk=0.00 edge case (geometric-mean compute_edit_risk zeros out whenever any of {propagation_cost, fan_in_norm, instability} is 0 for the cycle's max-risk member; on dagster's resolved 200+ module cycle every member happened to hit zero on one factor in the current graph)'. A stable module with instability=0 (Ce=0, all incoming edges) contributes factor=0, and 0^(1/3)=0 regardless of the other factors.
- **impact**: A resolved cycle involving a stable load-bearing module appears to have zero risk in the summary, even though resolving a 200-module cycle is highly significant. It gets buried in the improvements list and could be treated as a low-priority positive change when it should rank high.
- **fix**: Use a Euclidean norm or additive aggregation instead of geometric mean for cycles (since cycles should amplify risk, not zero it). Alternatively, add a small epsilon floor (e.g., 0.01) to each factor before the geometric mean to avoid true zeros, or handle the cycle case specially with max(component) instead of product.
- **notes**: 
The bug is real and impacts diff/archy_diff summary ranking. Key scope items:

1. **Root cause**: Geometric mean optimization (require all three factors high for edit risk) is correct for the "is this module dangerous to edit?" question but wrong for "is resolving this cycle significant?" because it lets the geometric mean's zeroing behavior from stability mask structural significance.

2. **Empirical validation**: FUTURE.md explicitly cites dagster's 200+ module cycle where *every* member hit zero on one factor, producing risk=0.0 despite being load-bearing.

3. **Current status**: Explicitly marked as "Out of scope for v1, revisit later for implementation or explicit rejection" in FUTURE.md - this is a known deferred issue, not a hidden bug.

4. **Suggested fixes in finding**: Three options are reasonable:
   - Euclidean norm: sqrt(prop^2 + fan_in_norm^2 + inst^2) - avoids zeroing, but changes scale
   - Epsilon floor: max(0.01, prop) * max(0.01, fan_in_norm) * max(0.01, inst) - prevents exact 0, adds tuning knob
   - Cycle-specific path: Use max(components) for cycles instead of geometric mean, while keeping geometric mean for new-cycle-added regressions

5. **Impact on improvements ranking**: Line 276 sorts by (-i.risk, i.description), so risk=0.0 resolved cycles will sort below any improvement with risk>0, potentially burying significant resolutions.

6. **Design tension**: For single-module edit risk, stable modules should have low risk (SDP principle). For cycle resolutions, significance should not depend on whether the cycle happened to contain stable members. These are conflicting semantics folded into one metric.


### [minor] SDP violation identity ignores instability value changes, silently losing risk updates  (confirmed)
- **loc**: src/archy/diff.py:349-350 (_sdp_violation_set_diff _key function)
- **claim**: SDP violation identity is keyed only on (source, target), not on instability values. A violation that persists but whose source or target instability changes is not flagged as added/resolved.
- **evidence**: The _key function returns only (v.source, v.target). If baseline has a->b with I_a=0.9, I_b=0.1 and current has a->b with I_a=0.5, I_b=0.5, the violation will not appear in either added or resolved because the key (a,b) matches in both snapshots.
- **impact**: An agent cannot see that an SDP violation's risk profile has changed substantially (e.g., moved from 'high-risk edge to low-risk target' to 'moderate-risk edge to moderate-risk target'). The summary will be silent about this change even though it could be relevant to the agent's evaluation of the diff.
- **fix**: Change the identity key to include the rounded instability values: `(v.source, v.target, round(v.source_instability, 2), round(v.target_instability, 2))`, or add a separate 'modified SDP violations' category for edges that persist but change risk profile.
- **notes**: This is a visibility gap in the diff report for SDP violations. While the SDP checking and enforcement work correctly, an agent using the diff report to understand architectural changes would miss instability value changes on persisting violations. The agent would have to manually compare baseline and current snapshots to see these changes.

The suggested fix is sound: include rounded instability values (to 2 decimal places) in the identity key. This would make persisting violations with changed instability values appear as "resolved + added" in the diff, which follows the design pattern already used for cycles (a cycle that gains/loses modules appears as resolved + added).

Note that this issue is specific to SDP violations and does not affect regular layer violations, which don't have instability data. The issue location is confirmed at src/archy/diff.py lines 349-350 (the _key function inside _sdp_violation_set_diff).

### [minor] Cycle added/resolved symmetry treats member-modified cycles as both resolved and added  (confirmed)
- **loc**: src/archy/diff.py:323-329 (_cycle_set_diff function)
- **claim**: When a cycle gains or loses a member (e.g., baseline has {A,B}, current has {A,B,C}), the old cycle appears 'resolved' and the new cycle appears 'added', which is correct by design but can be confusing without explicit documentation
- **evidence**: The identity key is frozenset(c.modules). If {A,B} exists in baseline and {A,B,C} exists in current, frozenset({A,B}) != frozenset({A,B,C}), so both cycle_added and cycle_resolved will have an entry. The test suite (`test_diff.py`) documents this is intentional ('resolved + added rather than modified').
- **impact**: The diff output can show both 'cycle resolved: A, B' and 'cycle added: A, B, C' as separate items, which may confuse agents into thinking two independent changes occurred when in fact one cycle was modified. Low practical impact because the modules field makes the distinction clear.
- **fix**: Document this behavior explicitly in compute_diff's docstring or in the DiffReport class docstring. Alternatively, add a 'cycle_modified' item kind that captures member-only changes, though this would require richer cycle delta tracking.
- **notes**: CONFIRMED: Member-modified cycles appear as both "resolved" and "added" in diff output.

Key facts:
- Root cause: Cycle identity uses frozenset(modules), so {A,B} != {A,B,C}
- Documented at function level: compute_diff docstring (lines 160-165) acknowledges this design
- Agent visibility: diff_summary.py creates separate cycle_added/cycle_resolved items, which is explicit and clear
- Test gap: No explicit test case in test_diff.py for member-modified cycles
- Classes lacking docs: CycleSetDiff (lines 57-61) and DiffReport (lines 100-110) should document when both added and resolved can be non-empty

Recommended actions:
1. Add explicit test case to test_diff.py for member-modified cycle scenario
2. Add docstring to CycleSetDiff explaining that both added/resolved can be non-empty when membership changes
3. Update CycleSetDiff/DiffReport field docstrings
4. Enhance compute_diff docstring to note this manifests as separate summary items

Files: /Users/hosanglee/archy/src/archy/diff.py (lines 57-61, 100-110, 160-165), /Users/hosanglee/archy/tests/test_diff.py

## code / dsm


### [blocker] render_diff_text removed cells would cause IndexError if ever indexed with after.ordering  (confirmed)
- **loc**: src/archy/dsm.py:467-473, specifically if line 471 is changed to index after.ordering
- **claim**: If future code changes line 471 from just printing '(row, col)' to trying to look up node names via 'after.ordering[cell.row]' and 'after.ordering[cell.col]', it will crash with IndexError or return wrong node names, because the cell indices are into before.ordering, not after.ordering.
- **evidence**: Testing scenario: before.ordering has 5 nodes with removed edge at position (4,3). If after.ordering has only 3 nodes (some nodes removed), then after.ordering[4] raises IndexError. The code at line 455-457 (new_back_edges) and 462-464 (added) safely index after.ordering because those cells come from after_edges, but removed cells come from before_edges and would fail.
- **impact**: Any attempt to make render_diff_text more informative by printing removed edge names would crash. This creates a latent bug in the design where removed/weight_changed cells are incompatible with the after DSM passed to the rendering function.
- **fix**: Refactor diff_dsm to either (1) not include removed cells in DSMDiff, instead keeping them in a separate removed_edges dict keyed by (source_name, target_name), or (2) change DSMCell to store node names in addition to indices for cross-DSM compatibility, or (3) pass both before and after DSMs to render_diff_text.
- **notes**: 1. The bug is latent (doesn't crash currently) because render_diff_text line 471 only prints indices, not node names

2. Three realistic scenarios where this becomes a blocker:
   - Any enhancement to render_diff_text to print removed edge names (e.g., "a -> b" instead of "(2, 1)")
   - Extension of diff_dsm to support node filtering or subgraph selection
   - Any code that tries to interpret DSMDiff.removed cells without knowing they're indexed into before.ordering

3. The docstring for DSMDiff says "Structured diff between two DSMs over the same set of nodes (intersected)" but the code actually handles node additions/removals (lines 420-423), making the docstring misleading

4. Related issue: weight_changed also has indices from both orderings - stores (before_cell, after_cell) tuple where indices apply to different orderings

5. Suggested fixes align with finding:
   - Option A: Store removed edges as name tuples (source_name, target_name) instead of DSMCell indices
   - Option B: Add optional source_name/target_name fields to DSMCell 
   - Option C: Pass both before and after DSMs to render_diff_text
   
6. Current workaround: Must never attempt to index after.ordering with removed cell indices. This prevents making the renderer more informative.

7. Root cause: DSMCell class design doesn't encode which DSM's ordering its indices refer to, creating ambiguity when cells from different sources are mixed in DSMDiff

### [major] render_diff_text displays removed edges with indices from wrong DSM context  (confirmed)
- **loc**: src/archy/dsm.py:467-473
- **claim**: When rendering removed edges, the code prints cell row/col indices that come from the before DSM, but these indices are meaningless in the context of the after DSM passed to the function. An agent reading '(0, 1)' would assume it refers to after.ordering[0] and after.ordering[1], which is incorrect if nodes were removed or reordered.
- **evidence**: In diff_dsm line 418, 'removed.append(before_cell)' stores cells with indices into before.ordering. In render_diff_text line 470-471, these cells are printed as '(row, col)' without any indication that the indices are from before. Example: if before.ordering=('a','b','c') with removed edge at (0,1)='a->b', and after.ordering=('a','c'), printing '(0,1)' suggests 'a->c' which is wrong.
- **impact**: Agents or users reading the DSM diff output will see cell indices that don't correspond to meaningful positions in the after DSM context, leading to confusion about which edges were actually removed.
- **fix**: Either (1) store node names in DSMCell instead of just indices to preserve meaning across DSM contexts, or (2) reconstruct edge names in diff_dsm by keeping track of which edges were removed and storing them with their names, or (3) change render_diff_text to require both before and after DSMs so it can reconstruct the edge names from the before indices.
- **notes**: The ticket should capture:

1. **Root cause**: render_diff_text() receives only the after DSM but attempts to display removed cells that have indices from before.ordering. Without the before DSM, it cannot resolve edge names.

2. **Specific failure case**: When a node is removed (e.g., node 'b' in a graph with nodes a,b,c), the same cell indices can refer to completely different edges in before vs. after contexts. For example, (0,1) means "a -> b" in before but "a -> c" in after.

3. **Impact on agents**: LLM agents reading the text output will see "cell (0, 1)" with no context indicating these are before indices. They will likely misinterpret which edges were actually removed.

4. **Asymmetry issue**: This is made worse because added edges are rendered correctly with full names (lines 462-464: src = after.ordering[cell.row]; dst = after.ordering[cell.col]), creating an inconsistent API.

5. **Solutions**: 
   - Pass both before and after DSMs to render_diff_text() and resolve removed cell names using before.ordering
   - Alternatively, store node names in DSMCell instead of just indices (more disruptive change)
   - Or keep edge name information in DSMDiff itself (requires changing DSMDiff structure)

6. **Priority note**: JSON output (--format=json) avoids this issue, but the text output is the default and primary interface.

### [minor] DSMDiff docstring incorrectly states diff operates on 'intersected' nodes  (confirmed)
- **loc**: src/archy/dsm.py:67
- **claim**: The docstring for DSMDiff class says 'Structured diff between two DSMs over the same set of nodes (intersected)' but the actual code in diff_dsm includes nodes_added and nodes_removed, meaning the diff operates on the union of nodes, not the intersection.
- **evidence**: Lines 420-423 explicitly compute nodes_added and nodes_removed. The diff includes cells for new edges that reference nodes not in before.ordering, contradicting the 'intersected' claim.
- **impact**: Documentation is misleading about the scope of the diff. Users/agents might incorrectly assume removed edges only reference nodes that still exist in after.
- **fix**: Update docstring to reflect that the diff operates on the union of nodes from both DSMs, not the intersection.
- **notes**: The docstring should be updated from "over the same set of nodes (intersected)" to accurately reflect that the diff operates on the union of nodes from both DSMs. The code itself is correct and handles node additions/removals properly - this is purely a documentation accuracy issue. The rendering code in render_diff_text correctly handles removed edges by not dereferencing them with after.ordering, which demonstrates the developers understood this semantics even if the docstring didn't reflect it.

### [minor] SCC group labels skip numbers and duplicate DAG label in topological grouping  (confirmed)
- **loc**: src/archy/dsm.py:173-192, specifically line 189
- **claim**: In _group_by_topological, the SCC label is generated as f'SCC-{len(groups)}' where len(groups) includes the just-added DAG group. This causes SCC numbers to skip (e.g., SCC-1, SCC-3) when DAG groups are inserted. Additionally, multiple DAG groups can have the same label.
- **evidence**: If graph has pattern: singletons -> SCC -> singletons -> SCC, the groups would be labeled: DAG, SCC-1, DAG, SCC-3. The SCC-2 is skipped and both DAG groups have identical labels. Example trace: (1) add DAG group, len(groups)=1, (2) add SCC-1 (len was 1), (3) add DAG group, len(groups)=2, (4) add SCC-3 (len was 3).
- **impact**: Confusing group labels that don't clearly identify which SCC is which. The duplicate DAG labels might cause issues if code relies on unique group labels.
- **fix**: Maintain a separate SCC counter instead of using len(groups), like 'scc_count = 0' and increment it only for actual SCCs, so labels are SCC-0, SCC-1, etc., not SCC-0, SCC-2, etc.
- **notes**: CONFIRMED: Line 189 uses len(groups) for SCC labels, causing skipped numbers and duplicate DAG labels. Practical impact is moderate: (1) ASCII rendering hides the problem (doesn't display topological groups), (2) JSON output reveals confusion to LLM agents, (3) no code depends on label uniqueness. Recommended fix: Add scc_count = 0 counter, increment only for actual SCCs, use f'SCC-{scc_count}' instead of f'SCC-{len(groups)}'. This produces sequential numbers (SCC-0, SCC-1, ...) while leaving acceptable duplicate 'DAG' labels. The docstring at line 41 states groups "share" labels so duplicates are not inherently a contract violation, only the skipped numbering is problematic.

### [minor] weight_changed cells have mixed DSM indices but this is latent and untested  (confirmed)
- **loc**: src/archy/dsm.py:412-414
- **claim**: The weight_changed list stores (before_cell, after_cell) tuples where before_cell has indices into before.ordering and after_cell has indices into after.ordering. These are stored without warning that they're not directly comparable or indexable with a single DSM.
- **evidence**: Lines 412-414 append (before_cell, after_cell) where before_cell comes from before_edges and after_cell comes from after_edges. The test at line 285-292 only checks weights, not indices. No code currently uses weight_changed cells to index an ordering.
- **impact**: If future code tries to render weight_changed cells (currently not rendered), it would get confusing or wrong results. The design allows storing incompatible cell pairs.
- **fix**: Either document that weight_changed cells have mixed sources and shouldn't be indexed, or redesign to store only names/cell coordinates that work in after context, or pass both DSMs to rendering functions.
- **notes**: This is a legitimate design issue but currently benign:

ISSUE: weight_changed cells store (before_cell, after_cell) with indices into different orderings (before vs after). While the cells are identified by node names internally via _name_indexed(), the returned cells retain their original positional indices. If before.ordering != after.ordering, these mixed indices are incompatible.

EVIDENCE:
- Line 412-414: before_cell from before_edges, after_cell from after_edges  
- Line 392-393: _name_indexed(before) vs _name_indexed(after) create separate dicts
- docstring line 386-387: explicitly says "Operates on names...when reordering changes"
- test_diff_dsm_detects_weight_change (test_dsm.py:285-292): only checks weights
- render_diff_text: only shows count (line 448), never renders weight_changed cells

CURRENT IMPACT: None - weight_changed is never rendered or indexed in production code.

LATENT RISK: If render_diff_text or similar code is extended to show weight_changed cells (like it shows added/removed/new_back_edges), it would need to handle the mixed indices carefully. For example, rendering before_cell would need before.ordering, but render_diff_text only receives after DSM parameter.

SUGGESTED FIX: Either:
1. Store only node names in weight_changed instead of cells
2. Store only after_cell (and re-lookup before weights separately if needed)
3. Document that weight_changed cells have mixed sources and rendering requires passing both DSMs
4. Convert to (name_pair, before_weight, after_weight) tuple format

## code / graph+parser


### [major] Relative import resolution returns bare module names for escaped-package imports  (confirmed)
- **loc**: src/archy/graph.py:416-422, function _resolve_relative_base
- **claim**: When walk_up == len(src_parts) and suffix is non-empty, the function returns a module name without package prefix (e.g., 'other' instead of None), allowing invalid imports to be processed
- **evidence**: Test case: myapp/sub/__init__.py with 'from ...other import X'. qualname='myapp.sub', is_package=True, src_parts=['myapp','sub'], walk_up=2. Condition at line 416 checks '2 > 2' which is False, so execution continues to line 418. base = src_parts[:0] = []. target_parts = ['other']. target = 'other'. Returns 'other' instead of None. The check should be 'walk_up >= len(src_parts)' not 'walk_up > len(src_parts)' OR additional check when base is empty and suffix is not empty.
- **impact**: Relative imports that escape the package root (e.g., from ...other in myapp/sub) are incorrectly resolved to bare module names instead of being dropped. While these bare names likely won't match any real module in internal_qualnames, this corrupts the resolution logic semantics. The resolved imports produce silently-wrong behavior rather than correctly rejecting invalid relative imports.
- **fix**: Change line 416 from 'if walk_up > len(src_parts):' to 'if walk_up >= len(src_parts) or (base == [] and suffix):' to catch both the escaping case and the case where we'd produce an unprefixed module name
- **notes**: This bug only manifests when a PACKAGE (not a module) has a relative import that escapes the package root. Examples: `from ...name import X` from `pkg/sub/__init__.py` or `from ....name import X` from `pkg/sub/deep/__init__.py`. The current test suite uses `myapp.cli` which is a MODULE, so the bug escapes detection. The fix is to change line 416 from `if walk_up > len(src_parts):` to `if walk_up >= len(src_parts):`. This catches both cases: (1) walk_up > len(src_parts) and (2) walk_up == len(src_parts) with a suffix. The impact is that erroneous edges are added to the dependency graph pointing to bare module names instead of being dropped, corrupting the graph's semantic correctness and potentially affecting all downstream analysis that depends on the graph.

### [minor] Missing is_relative flag update when extending import edges  (confirmed)
- **loc**: src/archy/graph.py:500-505, function _add_or_extend_edge
- **claim**: When multiple ImportRefs resolve to the same source->target pair with different is_relative values, the edge's is_relative flag is not updated, preserving only the first import's value
- **evidence**: Lines 501-505 extend existing edges but do not update the 'is_relative' attribute. If ImportRef A (is_relative=False, module='pkg') and ImportRef B (is_relative=True, module='.') both resolve to the same target, whichever A or B is processed first will have its is_relative value retained. Line 510 sets is_relative when creating new edges, but extending at line 501 does not check or update this attribute.
- **impact**: Graph edges lose fidelity: the is_relative flag reports only the first import's resolution style, not whether any import to that target was relative. This affects graph consumers (mcp.py line 1343) that read is_relative to reconstruct edge metadata. While the resolved target is correct regardless, the metadata about import style becomes unreliable when multiple import statements target the same module.
- **fix**: When extending an edge at line 501-505, also check if ref.is_relative differs from data.get('is_relative'), and either: (1) update is_relative to True if any path is relative, (2) track both values, or (3) document that is_relative only reflects the first import processed
- **notes**: This is a real fidelity loss in graph edge metadata when multiple imports target the same module with different import styles (e.g., both `from pkg.b import x` and `from .b import y`). The correct semantic, per test comment at test_mcp.py:567, should be that is_relative=True if ANY contributing import was relative. Currently it retains only the first import's value. The bug is minor because: (1) it only manifests when the same module is imported multiple ways in the same file, and (2) the resolved target module is still correct regardless. However, consumers of the is_relative flag (e.g., agents reading edge metadata) would get incomplete information about whether relative imports were used. The test suite doesn't currently enforce the correct behavior - the assertion at test_mcp.py:569 just checks is_relative is a bool, not that it's the correct value.

## code / graph-algos


### [major] UnicodeDecodeError in git_churn not caught  (confirmed)
- **loc**: src/archy/hotspots.py:80-83
- **claim**: subprocess.run with text=True can raise UnicodeDecodeError on non-UTF8 filenames, but only CalledProcessError is caught
- **evidence**: The function calls subprocess.run(..., text=True, check=True) at line 81. If git log contains non-UTF8 encoded filenames in the output, Python will raise UnicodeDecodeError during the decode step, which is not caught by the except clause at line 82 that only catches CalledProcessError.
- **impact**: Projects with non-UTF8 filenames (e.g., Latin-1 encoded paths, or filenames with certain Unicode characters) will cause the hotspots command to crash with an uncaught exception instead of gracefully returning None as documented. This breaks the promise in the docstring that the function 'Returns None if root isn't inside a git repository or git isn't available'.
- **fix**: Catch UnicodeDecodeError in the exception handler: change 'except subprocess.CalledProcessError:' to 'except (FileNotFoundError, subprocess.CalledProcessError, UnicodeDecodeError):' at line 82. Alternatively, use errors='replace' when decoding to silently replace invalid bytes.
- **notes**: BOTH subprocess.run calls are vulnerable, not just the git log one:
1. Line 67-74: git rev-parse call also uses text=True but only catches FileNotFoundError and CalledProcessError
2. Line 80-83: git log call uses text=True but only catches CalledProcessError

The vulnerability affects:
- archy hotspots command (line 513 in cli.py) - raises ClickException on None, so it's somewhat protected
- archy what-to-refactor-next command (line 577 in cli.py) - does NOT check for None, passes directly to compute_refactor_priorities which handles None gracefully, but the crash still prevents the command from working

Real-world trigger: Any Python project with historical commits containing non-UTF8 filenames (e.g., Latin-1 encoded filenames, or binary filenames in git history).

Suggested fixes:
1. Add UnicodeDecodeError to exception handlers on both lines 73 and 82
2. OR use errors='replace' or errors='surrogateescape' in the subprocess.run calls to handle invalid sequences gracefully
3. Surrogateescape is preferred for filenames as it can round-trip the bytes

## code / history+trend


### [major] Integer fields reject valid float JSON values due to strict type checking  (confirmed)
- **loc**: src/archy/history.py:195-198, 175-183
- **claim**: _as_int() only accepts Python int type and rejects float values. JSON can represent integers as floats (e.g., 10.0, 20e0), causing valid JSONL rows to be silently dropped during parsing.
- **evidence**: In _as_int (line 195-198), the check `if isinstance(value, int)` fails for float type even when the float has no fractional part (e.g., 5.0). JSON numbers are parsed as Python float when written with decimal point (e.g., `{"module_count": 10.0}` becomes float in Python). When _row_from_dict calls _as_int(inputs["module_count"]) on line 175, a TypeError is raised and caught at line 185, silently skipping the entire row.
- **impact**: Manually edited JSONL files, corrupted data, or data from other systems that export integers as floats (e.g., API responses, Excel exports) will have their rows silently dropped from history. Users will lose historical trend data without warning. This is particularly dangerous during schema migrations or data imports.
- **fix**: Change _as_int to accept both int and float (when float has no fractional part): `if isinstance(value, (int, float)) and (isinstance(value, int) or value == int(value)): return int(value)`. Alternatively, document that only JSON integer literals are supported and add a test case for schema evolution with float-encoded integers.
- **notes**: This is a data loss bug affecting JSONL history files. Any valid JSON conforming JSONL with float notation for whole numbers (10.0, 20e0, etc.) will silently lose rows. Real-world triggers include: (1) Excel/CSV exports that represent all numbers as floats, (2) manual JSON editing with decimal points, (3) API responses using scientific notation, (4) data imported from systems outside archy. The fix should modify _as_int() to accept float values when they have no fractional part: `if isinstance(value, (int, float)) and (isinstance(value, int) or value == int(value)): return int(value)`. Additionally, a test case should be added to test_history.py to prevent regression, and documentation should clarify JSON number format requirements for manually-edited JSONL files.

### [major] Missing test coverage for pre-v0.7 JSONL rows without tangle_ratio field  (confirmed)
- **loc**: src/archy/history.py:178-181, tests/test_history.py
- **claim**: The code handles missing tangle_ratio by defaulting to 0.0 (via `inputs.get("tangle_ratio", 0.0)`), simulating backward compatibility with pre-v0.7 releases. However, there is NO test case verifying this works. There IS a test for missing complexity (pre-v0.20, test_pre_v0_20_row_without_complexity_reads_as_none), but no equivalent for tangle_ratio.
- **evidence**: Lines 178-181 explicitly document the v0.7.x tangle_ratio addition and provide a default. Tests exist for pre-v0.20 complexity (test_history.py:86-115). No test file contains any JSONL payload without the tangle_ratio key. The asymmetry suggests tangle_ratio backward compat may never have been exercised or validated.
- **impact**: If an old JSONL file from pre-v0.7 archy lacks tangle_ratio, the read path may fail silently or produce incorrect results. Trend rendering would show correct values (0.0 default), but the row count and order could be affected. This affects users upgrading from very old archy versions or attempting to merge historical data.
- **fix**: Add a test case `test_pre_v0_7_row_without_tangle_ratio_reads_as_zero` similar to the v0.20 test, writing a JSONL payload with tangle_ratio omitted from inputs dict and asserting the row reads successfully with tangle_ratio=0.0.
- **notes**: The finding is accurate. The code path at history.py:181 uses inputs.get("tangle_ratio", 0.0) which explicitly handles pre-v0.7 rows lacking the tangle_ratio field, but unlike the parallel pre-v0.20 complexity backward-compatibility test (test_pre_v0_20_row_without_complexity_reads_as_none at lines 86-115), there is no test case that validates this tangle_ratio backward-compatibility path is actually exercised and working. Additionally, src/archy/diff.py:235 has the same pattern (raw_inputs.setdefault("tangle_ratio", 0.0)) for snapshots without dedicated test coverage. The suggested action is appropriate: add test_pre_v0_7_row_without_tangle_ratio_reads_as_zero to match the complexity test pattern and ensure the backward-compatibility read path is validated. Note: While tangle_ratio is not rendered in trend output (trend.py:14-41 only displays overall, modularity, acyclicity, depth, equality, complexity), the field is part of HistoryRow and used by history read() and diff.compute_diff(), so validation is important for data integrity when reading old history files.

### [minor] Inconsistent null-value handling between optional_float and _as_float/_as_int  (confirmed)
- **loc**: src/archy/history.py:173-174, 218-223, 189-198
- **claim**: complexity field uses _optional_float which silently returns None for invalid types (line 221-223: `return None`), while tangle_ratio and other fields use _as_float/_as_int which raise TypeError. A JSONL row with `complexity: "invalid"` will parse successfully (complexity=None) but a row with `tangle_ratio: "invalid"` will be rejected entirely.
- **evidence**: _optional_float (lines 218-223) returns None for non-numeric types. _as_float (189-192) raises TypeError for invalid types. Lines 168-172 call _as_float for score fields. Line 174 calls _optional_float for complexity. Line 181 calls _as_float for tangle_ratio. If score dict has `{"complexity": "not a number"}`, row parses with complexity=None. If inputs dict has `{"tangle_ratio": "not a number"}`, the TypeError is caught (line 185) and row is skipped.
- **impact**: Corrupted or manually edited JSONL files with invalid complexity values are silently accepted (marked as None in trend table), while similar corruption in other fields drops the entire row. This creates inconsistent data quality behavior and makes debugging harder. Trend rendering accepts partial corruption.
- **fix**: Decide on forward-compat strategy: either (1) apply _optional_float consistently to ALL optional/evolved fields (complexity, tangle_ratio), OR (2) apply _as_float to complexity to fail-fast on corruption. Document which fields are optional vs required for schema evolution.
- **notes**: 1. The inconsistency exists but has limited practical impact since JSONL files are generated by archy code, not manually edited.

2. The root cause: _optional_float (and _optional_str) are designed to handle missing optional fields gracefully, but they also silently accept invalid type values. This is arguably over-permissive for data corruption detection.

3. Schema context: complexity is genuinely optional (`float | None = None`) because it was added in v0.20 (rows written by earlier archy versions don't have it). The code correctly uses _optional_float to support backward compatibility with missing fields. However, _optional_float silently accepting invalid types is a separate concern.

4. Historical precedent: commit and branch (also optional strings, lines 166-167) use _optional_str which has the same silent degradation behavior, so the pattern is consistent across the codebase.

5. The fix options are:
   - Option A (consistency via _optional_float): Apply _optional_float to tangle_ratio's direct value too (but note it already has a 0.0 default for missing fields)
   - Option B (consistency via strict validation): Change _optional_float/_optional_str to raise on invalid types instead of silently returning None. This would be a breaking change if anyone has corrupted JSONL files.
   - Option C (hybrid): Keep missing-field handling graceful but add logging/validation for obviously-wrong types (e.g., distinguish None from "field present but corrupted")

6. The trend.py code explicitly handles `complexity = None` (rendering as "-"), showing the design accounts for optional fields. No similar handling for tangle_ratio being None, confirming tangle_ratio is meant to always have a value.

### [minor] No validation of timestamp format in JSONL rows  (confirmed)
- **loc**: src/archy/history.py:154-162, src/archy/trend.py:31
- **claim**: timestamp is accepted as any string (line 161 only checks `isinstance(timestamp, str)`) with no format validation. render_text (line 31) assumes ISO-8601 format when it slices `timestamp[:16]` and replaces 'T' with space. Invalid timestamps silently corrupt the trend table display.
- **evidence**: HistoryRow accepts any string for timestamp. _row_from_dict checks only that timestamp is a string (line 161). When render_text processes a timestamp like 'xyz' or 'invalid-format', line 31 produces `when = 'xyz'[:16].replace('T', ' ') = 'xyz'`, corrupting the column alignment in the trend table. No exception is raised.
- **impact**: Manually edited JSONL or corrupted data with malformed timestamps will silently corrupt the trend output table formatting. Users will see misaligned columns without understanding why. The scoring data is valid but the presentation is broken.
- **fix**: Add timestamp format validation in _row_from_dict using regex (e.g., `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`). Reject rows with invalid timestamps rather than silently accepting them. Alternatively, sanitize timestamps in render_text with better slicing logic (check length before indexing).
- **notes**: This is a genuine validation gap in data deserialization with display-side effects. The risk is low in production (normal users don't hand-edit JSONL) but real during manual history merging/cleanup. Key facts for ticket: (1) Normal code path via row_from_score() always produces valid ISO-8601 timestamps, so this only manifests with manual JSONL editing; (2) The corruption is display-only (column alignment breaks)—scoring data remains valid and unaffected; (3) No timestamp values are ever parsed, calculated, or used beyond the display formatting in render_text; (4) Code comment at history.py:23 documents the expected format but provides no enforcement; (5) The suggested fixes are viable: either add regex validation in _row_from_dict (better, fail-fast) or add length-checking in render_text's slicing (defensive, permissive). Consider adding a test case with a manually-crafted invalid timestamp to verify the render_text behavior change if a fix is applied.

### [minor] JSONL append uses two separate write() calls instead of atomic single write  (confirmed)
- **loc**: src/archy/history.py:44-48
- **claim**: append() writes JSON and newline in two separate fh.write() calls. If the process crashes or is killed between the two writes, the JSONL file will have a JSON object without a trailing newline, potentially corrupting the next line when it's appended.
- **evidence**: Lines 46-48 show two write calls: `fh.write(json.dumps(...))` followed by `fh.write("\n")`. On Unix-like systems with Python's text mode, these may not be buffered atomically. The read() function (lines 55-68) uses splitlines() which handles this by skipping malformed lines, but data loss occurs silently.
- **impact**: In high-frequency recording (e.g., agent loop calling archy score --record repeatedly), a process crash or timeout could lose the last recorded point and potentially corrupt the preceding line. The error handling in read() prevents crashes but loses data. Historical trend analysis becomes unreliable if multiple points are lost over time.
- **fix**: Combine the two writes into a single string: `fh.write(json.dumps(...) + "\n")` to minimize the window between multiple write operations. Alternatively, use Python's atomic file writing pattern (write to temp file, then rename). Add a test that simulates a write crash and verifies JSONL integrity.
- **notes**: 1. The issue is REAL and CONFIRMED through testing. Two separate write() calls create a vulnerability window.

2. SPECIFIC FAILURE MODE: If a process is killed/crashes between write(JSON) and write("\n"), the JSON lands in the file without a trailing newline. When the next process appends normally, the two JSONs merge on one line, creating unparseable JSONL that causes SILENT DATA LOSS (not corruption that crashes the reader).

3. IMPACT SCOPE: The data loss is limited to history.jsonl records. It doesn't corrupt other data or crash the application—it just silently drops trend data points. In high-frequency recording (e.g., agent loops), this could accumulate over time.

4. EXISTING MITIGATION: The read() function (lines 61-64) has error handling that skips malformed lines with a comment explicitly acknowledging "a half-flushed write should not break trend." However, this mitigation only prevents crashes—it doesn't prevent the silent data loss.

5. SUGGESTED FIXES: The finding's recommendation is sound—combine writes into a single string: `fh.write(json.dumps(...) + "\n")` to reduce the window. However, even better would be:
   - Use explicit fh.flush() after write if immediate durability matters
   - Or use atomic file writing (temp file + atomic rename) for guaranteed consistency
   - Or use `with open(..., newline='')` in binary mode and write bytes

6. TEST GAP: There's currently no test for the crash-between-writes scenario. The test_malformed_lines_are_skipped() test manually writes bad JSON to the file but doesn't simulate the specific "JSON without newline followed by normal append" scenario.

### [nit] git_metadata returns incomplete tuple on partial git command failure  (confirmed)
- **loc**: src/archy/history.py:98-107
- **claim**: git_metadata calls two separate git commands (rev-parse HEAD and rev-parse --abbrev-ref HEAD). If the first succeeds but the second fails, it returns (commit_sha, None). This creates asymmetric git context metadata where branch might be None even though the path is in a valid git repo.
- **evidence**: Lines 102-103 call _git twice independently. If rev-parse HEAD succeeds (returns commit), and rev-parse --abbrev-ref HEAD times out or fails (returns None), the function returns (commit, None). The docstring (line 99) says 'best-effort' but doesn't specify the behavior when one call succeeds and one fails. Tests verify both success and both-None cases but not the mixed case.
- **impact**: When recording history, rows may have commit but no branch metadata. This creates incomplete context in trend analysis. For CI/CD systems where one git command might be slow/unreliable, users could lose branch information while keeping commit info, making trend analysis less useful for branch-specific comparisons.
- **fix**: Document the expected behavior explicitly (return (None, None) on ANY git command failure vs return partial results). If atomicity is desired, either: (1) retry both commands together, or (2) return (None, None) if either fails. Add a test case that mocks one git command failing while the other succeeds.
- **notes**: This is a documentation-code contract mismatch and test coverage gap rather than a functional bug. The docstring implies atomic behavior (both values or both None), but the code allows independent None values. The practical impact is minimal because: (1) asymmetric failure is unrealistic given both commands run in same git context with a 2-second timeout; (2) consuming code (trend.py line 32, history.py serialization) already handles independent None values correctly—no code assumes atomicity. The ticket should capture: (1) Fix docstring to accurately describe the actual behavior: "Returns a tuple (commit_sha, branch_name) where either or both may be None on command failure"; (2) Add a unit test mocking one git command succeeding while the other times out, to document and protect the asymmetric case; (3) Consider whether atomicity is desired (retry both together if either fails) or if current independent behavior is acceptable (likely acceptable given downstream code handles it)

## code / index+watcher


### [major] Uncaught ParseResult.model_validate_json() deserialization error crashes sync  (confirmed)
- **loc**: src/archy/index.py:138, line 150
- **claim**: model_validate_json() is called without try/except; if parse_json is corrupted or schema-incompatible, it raises ValidationError and crashes the entire sync, leaving the cache inaccessible
- **evidence**: Lines 138 and 150 call ParseResult.model_validate_json(row['parse_json']) directly. If the stored JSON doesn't match the current ParseResult schema (due to schema drift, corruption, or partial write), Pydantic raises ValidationError. There is no exception handler wrapping this call in the sync() function (only OSError handlers exist for file I/O). The crash propagates to callers like build_graph_cached(), blocking builds.
- **impact**: If a cache row's parse_json becomes invalid (e.g., due to schema mismatch between code versions), every subsequent build that touches that row crashes. Users must manually delete the cache to recover. This violates the 'disposable cache' property claimed in the docstring.
- **fix**: Wrap the model_validate_json() calls in try/except, catching ValidationError. On deserialization failure, either re-parse the file (treating the cached entry as stale) or skip it and let the cache row be refreshed on the next sync.
- **notes**: Key points for the ticket:

1. **Affected paths:** Both the main build path (`build_graph_cached()` → `sync()`) and the watcher's on-demand path (`IndexManager.build_graph()` → `sync()`) are vulnerable. Only the watcher's background debounce thread is protected via `contextlib.suppress(Exception)`.

2. **Trigger scenarios:** 
   - Partial write during cache update (OS crash, disk full, process killed mid-write)
   - Schema drift between code versions (field renamed, type changed in ParseResult or its nested types)
   - Cache corruption from concurrent access (though less likely due to SQLite locking)

3. **Current recovery:**
   - Users must manually delete `.archy/index.db` to recover
   - There is no automatic detection or graceful degradation

4. **Suggested fix:** Wrap `model_validate_json()` calls in try/except(ValidationError) and either:
   - Treat the corrupted row as stale (re-parse the file)
   - Skip it and let the entry be refreshed on next sync
   - Log a warning about cache corruption

5. **Test coverage:** No existing test exercises schema mismatch or deserialization failure. The test at `test_parse_json_is_valid_json` only verifies that stored JSON is parseable by the json module, not that it deserializes to a valid ParseResult.

### [major] No schema version bump protection for ParseResult changes  (confirmed)
- **loc**: src/archy/index.py:35
- **claim**: SCHEMA_VERSION is decoupled from ParseResult schema; changing ParseResult without bumping SCHEMA_VERSION creates silent cache corruption for old installations
- **evidence**: SCHEMA_VERSION = 1 gates cache invalidation in open_index() (lines 71-73). But the ParseResult class definition in parser.py is NOT versioned. If a developer modifies ParseResult (e.g., adds a field with default, removes a field, changes a type) without bumping SCHEMA_VERSION, old cached parse_json rows will fail to deserialize on the next version. There is no compile-time link between the two.
- **impact**: Developer error when modifying ParseResult can silently corrupt caches of users running old and new archy versions together (e.g., in a mono-repo with multiple tools). The cache becomes unreadable until manually deleted.
- **fix**: Add a runtime check in open_index() or sync() to verify ParseResult can round-trip a sample parse_json, failing fast if schema mismatch is detected. Alternatively, document that SCHEMA_VERSION MUST be bumped whenever ParseResult is modified, and add a test that enforces this coupling.
- **notes**: The ticket should clarify scope: this is a LATENT ARCHITECTURAL RISK, not an active bug. The evidence is correct about the decoupling and the risk mechanism, but the current state is safe. Suggested fix remains valid - either add runtime schema validation in sync() that test-deserializes a sample row, or add a test that enforces SCHEMA_VERSION must bump when ParseResult.model_json_schema() changes. The actual impact would be developer-time cost (users have to delete .archy/index.db) but the silent nature of the failure makes it a correctness hazard worth preventing.

### [major] Concurrent edits to same file can produce inconsistent cache state  (confirmed)
- **loc**: src/archy/index.py:143-145
- **claim**: If a file is modified while _sha256() is reading it, the computed hash is for a partial/inconsistent version; combined with stale mtime, cache can diverge from reality
- **evidence**: At line 143, _sha256() reads the file with path.read_bytes(). If the file is being written to by another process while read_bytes() executes, the hash is for a partial or inconsistent version. The stat() at line 126 already succeeded, so the code assumes the file is stable. If a file is written atomically (rename) this is not an issue, but in-place edits can lead to reads of intermediate states.
- **impact**: For non-atomic file writes, the cache can store (correct_parse, hash_of_partial_content). On the next sync, if the file hasn't changed, the hashes match and the parse is reused. But if the file is edited again, the hash diverges and causes unnecessary re-parsing. This is a coherency issue, not correctness.
- **fix**: This is difficult to solve without OS-level file locking. Consider documenting that archy assumes atomic file writes (editors should use temp-file-then-rename pattern). Alternatively, detect by comparing parse_result.imports with the actual file content, but this is expensive.
- **notes**: The finding's location (lines 143-145) and explanation about torn reads is technically accurate but misleading. The actual bug is at lines 137-140 where the mtime+size optimization returns stale cached parses without checking sha256. The issue requires: (1) file edit that preserves size, (2) mtime granularity or forced mtime persistence. This is a CORRECTNESS bug for dependency graphs, not just a coherency/performance issue. The fix should either: (a) always check sha256 after stat(), not just when mtime+size change, or (b) use sub-second mtime precision, or (c) add file content versioning beyond just stat. Atomic file writes (temp+rename) don't prevent this because they don't affect mtime/size detection - the bug is in the cache logic itself, not in concurrent writes. Location should be expanded to include line 137 early return logic, not just the _sha256 function.

### [minor] Stale mtime stored in cache when file is edited mid-sync  (confirmed)
- **loc**: src/archy/index.py:134, 164
- **claim**: The mtime and size are captured at line 134 (from stat()); if a file is edited between stat() and parse_file(), the INSERT at line 164 stores the old mtime with the new content, causing mtime/sha256 mismatch
- **evidence**: Line 126 captures stat(), storing mtime=M and size=S. Between line 134 and line 155 (parse_file()), the file can be edited, changing its actual mtime/size on disk. Line 164 inserts the parse result with the cached mtime=M (not the current mtime). On the next sync, the on-disk mtime has advanced but the cache still has M, triggering unnecessary re-hashing (the sha256 will match and reuse the parse). This causes a cache coherency hiccup but not a correctness error.
- **impact**: For files edited during a sync, the next sync does extra hashing work (re-computing sha256) even though the cache is valid. In high-latency tree-sitter parsing, this could delay graph rebuilds slightly. Not a functional bug, but a missed optimization opportunity.
- **fix**: Capture mtime/size after parse_file() completes rather than before, or re-stat the file after parsing to get the final mtime. This ensures the cache stores the mtime that was actually committed to disk.
- **notes**: CONFIRMED as described. The issue is a **cache metadata coherency optimization miss**, not a correctness bug. Impact: On the next sync after a file is edited during parsing, one sha256 computation is done unnecessarily (because the cached sha256 is correct but cached mtime/size are stale, so line 137 check fails but line 146 succeeds).

The suggested fix is sound: capture mtime/size AFTER parse_file() completes (or re-stat the file) to ensure the metadata in the cache matches the actual file state when the content was read. This would eliminate the unnecessary re-hashing.

Current behavior is correct but suboptimal. The race condition is narrow (file edit between stat and parse completion) but possible, especially with the 2-second debounced watcher. No test case exists for concurrent edits mid-sync.

## code / install


### [blocker] Claude plugin state change breaks install/uninstall symmetry  (confirmed)
- **loc**: src/archy/install/adapters/claude.py:106
- **claim**: The `plugin_installed()` check in `plan()` causes the install and uninstall plans to become asymmetric when the plugin is installed between the two operations, violating the idempotency contract.
- **evidence**: Scenario: (1) User runs `archy install` without plugin → plan includes MCP config + instructions. (2) User installs the Claude plugin. (3) User runs `archy uninstall` with plugin installed → plan omits MCP config + instructions. Result: MCP config and instructions files are left behind after uninstall, even though they were added by the earlier install. The spec requires 'install -> uninstall round-trip leaves no file mentioning archy on a previously-clean machine', but this violates that by leaving uninstalled MCP stanzas.
- **impact**: User who installs archy, then installs the Claude plugin, then uninstalls archy will be left with orphaned MCP config and instruction files. Running `archy uninstall` again (after plugin was installed) will not remove these files, violating the idempotency contract. If the plugin is later uninstalled, the orphaned MCP config could re-activate archy accidentally.
- **fix**: Make the plan deterministic by detecting the plugin state once per run (in the runner, before any adapters run) and passing it to all adapters, rather than calling `plugin_installed()` each time `plan()` is called. Or alternatively, always include MCP and instructions in the plan regardless of plugin state, and document that the plugin and manual config are compatible (plugin's MCP stanza + manual instructions both present is fine).
- **notes**: The fundamental issue is that `plan()` is called at execution time for both install and uninstall, so its output depends on the plugin state at that moment, not the state when install originally ran. The spec requires uninstall to be the exact inverse of install (SPEC_INDEX_AND_INSTALL.md line 246: "It is the inverse rather than a separate code path").

Two solution approaches mentioned in the finding are both valid:
1. Detect plugin state once per runner invocation (before any adapters run) and pass it to all adapters, decoupling the plan from runtime state
2. Always include MCP and instructions in the plan regardless of plugin state, documenting that having both (manual config + plugin MCP) is acceptable

The finding should note that the issue affects only Claude adapter (other adapters don't call a time-dependent function in plan()). The permissions action is correctly always included since permissions aren't duplicated by the plugin.

A regression test must be added that installs, then simulates plugin installation, then uninstalls, verifying zero "archy" references remain in all files.

### [minor] Type mismatch in permissions array not symmetrically handled  (confirmed)
- **loc**: src/archy/install/merge.py:142-144, 208-214
- **claim**: If the user's config has `permissions.allow` as a non-list type, `render_claude_permissions` converts it to an empty list `[]`, but `strip_claude_permissions` does not restore it to the original non-list form, breaking the round-trip property.
- **evidence**: In `render_claude_permissions`, line 142-144: if `allow` is not a list, it's replaced with an empty list. In `strip_claude_permissions`, line 210: the code only modifies `allow` if it's already a list. So if a user has `{"permissions": {"allow": "some string"}}` and runs install then uninstall, after round-trip it becomes `{"permissions": {"allow": []}}`. The scalar value is lost. Test case: starting with `{"permissions": {"allow": "not a list"}}`, render produces `{"permissions": {"allow": ["mcp__archy__...", ...]}}`, and strip produces `{"permissions": {"allow": []}}` (not the original scalar).
- **impact**: Users with malformed Claude config (if such config could exist) would have their broken structure left in a different broken state after uninstall. Low practical impact because: (1) users are unlikely to have `allow` as a scalar, (2) the config was already malformed, (3) archy's entries are still correctly removed. However, it violates the principle that uninstall should leave unrelated config 'byte-for-byte' as found.
- **fix**: Make `strip_claude_permissions` symmetric with `render_claude_permissions` by only processing the `allow` array if it was successfully created/populated during render. Alternatively, add a test case to verify round-trip behavior on non-list `allow` values and document the edge-case handling.
- **notes**: This is a real asymmetry in the pure render/unrender functions that violates the stated cardinal rule of "a user's unrelated config survives untouched". However, the practical impact is minimal because: (1) users are extremely unlikely to have permissions.allow as a non-list type in valid Claude configs (it's documented as an array), (2) the malformed config already violates the schema, and (3) archy's permission patterns are still correctly removed. The ticket should note that the functions treat allow as archy-managed (since render appends to it), but don't have state tracking to remember what was replaced. A proper fix would require either: storing metadata about original type, or not attempting to normalize non-list allow values at all and failing/warning instead. Additionally, add test case test_strip_claude_permissions_round_trip to verify round-trip behavior with various input types, similar to test_strip_round_trip_restores_unrelated_config for json_mcp.

### [minor] Invalid config types silently overwritten instead of flagged  (confirmed)
- **loc**: src/archy/install/merge.py:65-75
- **claim**: The `_ensure_dict` function silently overwrites non-dict values at required keys (e.g., if `mcpServers` is a string instead of a dict), replacing them with an empty dict. This could mask configuration errors and lose user data if the non-dict value was intentional.
- **evidence**: Code: if a user's config has `{"mcpServers": "some value"}` (scalar instead of object), calling `_ensure_dict(obj, "mcpServers")` will replace the string with `{}` without warning. The string value is lost. This applies to any shared config key: `mcpServers`, `mcp`, `mcp_servers`, `permissions`.
- **impact**: Users with malformed or unusual config structures could lose data if they have scalar values where archy expects dicts. Probability is low because standard config files should have the expected types, but any user who has manually edited their config could be affected. Impact is medium because data loss occurs silently without error messaging.
- **fix**: Raise a clear `InstallError` when a required config key has an unexpected type (non-dict where dict is required), rather than silently overwriting. This fails fast and gives users actionable feedback to fix their config before install proceeds. Alternatively, log a warning and recover gracefully.
- **notes**: The issue is confirmed but with context on likelihood: (1) Most users won't manually create scalar values at these keys since they follow the documented JSON schema. (2) However, users doing manual config edits (documented in docs/INSTALL.md lines 80-93) could make this mistake. (3) The silent data loss is the core problem - no error, warning, or logging alerts users their value was overwritten. (4) No test coverage exists for malformed nested key types. (5) Suggested fix: Raise InstallError with clear message (e.g., "Invalid config: mcpServers must be a dict, found string") instead of silently overwriting. The "minor" severity is appropriate because the probability of occurrence is low (standard configs should be well-formed), but users who manually edit config files could encounter this. Impact is medium because data loss occurs silently.

## code / layers+contracts


### [major] Root package extraction allows invalid Python identifiers from malformed layer patterns  (confirmed)
- **loc**: src/archy/contracts.py:219-225
- **claim**: When deriving contracts from archy.yaml, the root package extraction splits patterns on '.' and takes the first component without validating that it's a valid Python identifier. Malformed patterns produce invalid root names that cause import-linter to fail with cryptic errors.
- **evidence**: Pattern `"**"` would yield root package `"**"`. Pattern `"*foo"` would yield `"*foo"`. Pattern `".foo"` would yield empty string. When these are passed to import-linter as `root_packages: ["**"]`, import-linter fails with ModuleNotFoundError, which is confusing because the real problem is the malformed layer pattern.
- **impact**: Users who accidentally write patterns that don't start with a valid identifier (e.g., `modules: ["**"]` or `modules: ["*"]`) get confusing error messages from import-linter instead of a clear validation error from archy. The fallback mechanism that converts archy.yaml to contracts becomes less reliable.
- **fix**: Validate extracted root packages using `str.isidentifier()` and raise a clear LayerConfigError if any root is invalid. Example: 'could not infer valid root_package from patterns: extracted invalid identifier "**". Ensure layer patterns start with a valid Python package name (e.g., "myapp.domain.*" not "**").'
- **notes**: The validation gap exists at TWO potential points: (1) during pattern parsing in `_parse_layers` (more proactive, prevents invalid patterns from being stored), or (2) during root extraction in `_archy_yaml_to_user_options` (catches the specific case of root packages being passed to import-linter). The finding correctly identifies the contracts.py location as the critical point where the fallback breaks. However, a more comprehensive fix might validate patterns upfront in layers.py to prevent invalid patterns from being silently accepted into the config in the first place. The error message should reference the documented pattern syntax (dotted-name globs starting with a valid Python identifier) to help users understand what's wrong with their config.

### [major] config_filename parameter not validated to be a file  (confirmed)
- **loc**: src/archy/contracts.py:107-112
- **claim**: When a custom config_filename is provided to run_contracts(), the code checks exists() but not is_file(). If the path is a directory, import-linter receives the directory name as a config filename and fails with a misleading error.
- **evidence**: At line 109, the check is `if not config_path.exists()` without verifying `config_path.is_file()`. If a user passes a directory path, line 176 extracts `config_path.name` (just the directory name) and tries to read it as a config file in the wrong location. import-linter would fail with 'file not found' pointing to the directory name, not explaining that a directory was passed instead of a file.
- **impact**: Poor error messages when users accidentally pass directory paths instead of file paths. The failure is not data-corrupting but makes the API confusing.
- **fix**: Add a check after line 109: `if not config_path.is_file(): raise ContractsConfigError(f'config_filename must be a file, not a directory: {config_path}')`
- **notes**: The bug is confirmed at /Users/hosanglee/archy/src/archy/contracts.py lines 107-112. Users passing a directory path as config_filename receive an unhelpful "Could not find [dirname]" error instead of clear guidance. The fix should add `if not config_path.is_file(): raise ContractsConfigError(...)` after line 109. Additionally, the test suite should include a test case verifying that directory paths are rejected with a clear error message (no such test exists in tests/test_contracts.py). The error is not caught by the CLI handler (cli.py:761) which only catches ContractsConfigError/ContractsNotAvailable, so the fix will also improve error handling consistency.

### [minor] Pattern matching translates `**` not preceded by dot to incorrect regex  (confirmed)
- **loc**: src/archy/layers.py:313-342, specifically line 331
- **claim**: When `**` appears without a preceding dot in a pattern, the code generates `r".*"` which matches any characters including dots, violating the documented semantic that `**` matches 'zero or more dotted segments'
- **evidence**: Pattern `foo**bar` is translated to `^foo.*bar$` instead of `^foo(?:[^.]*\.)*[^.]*bar$`. This regex would match `foo.anything.bar` when it should only match `fooXbar` where X is a single segment without dots. The line `parts.append(r".*")` at line 331 is unconditional for `**` not after a dot, making it semantically incorrect for dotted-name globs.
- **impact**: Users who write malformed patterns like `a**b` or `**c` would get silent false-positive or false-negative violations because the regex doesn't respect segment boundaries. Since the documentation specifies dotted-name glob semantics and all examples use `pkg.**` format, this is an edge case but still a correctness bug.
- **fix**: Either (1) validate patterns to reject `**` not immediately after a dot or at the start, raising a clear error message, or (2) correct the regex generation so `**` always respects segment boundaries: when not after a dot, use a pattern like `(?:[^.]*\.)*[^.]*` or similar
- **notes**: 1. **Root Cause**: Line 331 uses unconditional `r".*"` for `**` not after a dot, but `.*` matches any characters (including non-dots), violating "zero or more dotted segments" semantics

2. **Affected Patterns**: Non-canonical patterns where `**` appears:
   - In the middle without dots: `foo**bar`
   - At the start without trailing dot: `**foo`
   - These patterns generate semantically incorrect regex
   
3. **Why It's Minor Not Major**:
   - Only canonical form documented: `pkg.**` (handled correctly at lines 327-329)
   - All examples use canonical form (README, tests, config files)
   - Non-canonical patterns are undocumented and untested
   - No validation rejects such patterns, but no users appear to write them
   
4. **Recommended Fix**:
   - Option A (stricter): Validate at config load time - reject `**` not after a dot or at pattern start, with clear error message
   - Option B (permissive): Fix regex generation to use segment-aware pattern like `(?:[^.]*\.)*[^.]*` for non-canonical cases
   - Consider which intent: is `foo**bar` meant to be supported but undocumented, or unsupported and should error?

5. **Test Gap**: No test coverage for non-canonical patterns (add test cases for `a**b`, `**c` to clarify intent)

6. **Code Location**: `/Users/hosanglee/archy/src/archy/layers.py` lines 313-342, specifically line 331

### [minor] Misleading comment claims layer disjointness is enforced at load_config, but validation is deferred to runtime  (confirmed)
- **loc**: src/archy/layers.py:137-145
- **claim**: The docstring at line 137 claims 'Layers are required to be disjoint (enforced at load_config)' but load_config() does not validate disjointness. Validation only occurs at runtime when match_layer() is called.
- **evidence**: The _parse_layers() function (lines 207-224) only parses patterns into LayerSpec objects without checking for overlaps. The disjointness check is deferred to match_layer() (lines 141-145) which raises an exception only when a module actually matches multiple layers during find_violations().
- **impact**: Confusing documentation. Users might assume overlapping patterns are caught early during config load and be surprised by runtime errors. Low practical impact since the validation still happens, just later.
- **fix**: Either (1) move the disjointness validation to load_config() to match the claim, or (2) update the comment to say 'checked at runtime in match_layer when a module matches multiple layers'
- **notes**: The finding is accurate. The docstring comment at line 137 is misleading. Users reading the code might assume that load_config() validates layer pattern disjointness and will catch overlapping patterns early, but validation is actually deferred to runtime when match_layer() is called during find_violations(). The practical impact is low since validation still occurs, just at a different time. The ticket should capture: (1) The misleading docstring location: src/archy/layers.py line 137-138; (2) The actual validation location: lines 140-145 in match_layer(); (3) When it occurs: at runtime during find_violations(), not at config load time. Two remedies are viable: either add disjointness validation to load_config() and _parse_layers() to match the claim, or update the docstring to accurately document the runtime validation behavior.

## code / mcp


### [blocker] IndexManager cache key ignores config changes, returns stale manager with old kwargs  (confirmed)
- **loc**: src/archy/mcp.py:1491-1507, specifically line 1500
- **claim**: The _manager_for function caches IndexManager instances by root path only (str(root)), ignoring the ignored_dirs and extra_roots kwargs. Subsequent calls to the same root with different kwargs silently reuse the cached manager initialized with the old kwargs.
- **evidence**: Line 1500: key = str(root) - cache key includes only the resolved root path. Lines 1502-1507: _MANAGERS.get(key) returns cached manager without checking if kwargs match. If archy.yaml is modified between tool calls (e.g., changing the 'exclude' list), a new call to archy_snapshot with different kwargs will hit the cache and reuse a manager initialized with the old ignored_dirs.
- **impact**: Silent wrong answers: when archy.yaml changes between MCP calls, subsequent tool calls build graphs with stale exclude/roots configuration. Cycles, violations, scores, and impact analysis become incorrect, with no indication to the agent that the configuration changed. A single agent session could accumulate diverging graph state based on call order and config mutations.
- **fix**: Make the cache key include a hash of (ignored_dirs, extra_roots), or disable caching of the kwargs and always pass them through from _graph_kwargs on each call. Alternatively, validate kwargs match the cached manager's state before returning it.
- **notes**: The ticket should capture: (1) Cache key design flaw: _MANAGERS keyed only by root path, not config state. (2) Stale manager scenario: With persistent MCP server + agent workflows that modify archy.yaml, the manager's stored ignored_dirs/extra_roots become obsolete. (3) Silent failure: No indication to agent that config changed; graph results diverge silently based on call order. (4) Scope: All tools using _load_graph/_build_graph are affected (cycles, check, score, impact, affected, snapshot, etc.). (5) No safety net: Watcher doesn't watch archy.yaml, no cache invalidation on config change. (6) Fix options should include: (a) Include config hash in cache key, (b) Store config state in manager and validate before returning, (c) Pass kwargs through on every call (not just first creation), or (d) Reload config on each graph build. The docstring at line 1496-1497 explicitly states "kwargs are honored on first creation" which documents the limitation but doesn't prevent the bug.

### [major] Unhandled exceptions in parameter validation raise instead of returning error payloads  (confirmed)
- **loc**: src/archy/mcp.py:1178-1180, 1229, 1359, 1397, 1422-1424, and 981-982
- **claim**: Tools validate parameters by raising ValueError or LayerConfigError instead of returning structured error payloads. The agent receives exception messages instead of consistent error objects.
- **evidence**: archy_graph_focus raises ValueError at lines 1178, 1180 if direction is invalid or depth < 0. archy_graph_summary raises ValueError at line 1229 if top_n <= 0. Similar raises in archy_hotspots (1359), archy_high_risk_modules (1397), archy_what_to_refactor_next (1422-1424), and archy_check (981-982 raises LayerConfigError). Contrast: archy_contracts returns ContractsPayload with error field (1014-1016), archy_diff returns DiffErrorPayload (1090-1092), archy_dsm returns DSMErrorPayload (885).
- **impact**: Agent cannot uniformly handle errors. Tools like archy_contracts, archy_diff, archy_dsm return error payloads with a predictable shape, but tools like archy_graph_focus, archy_graph_summary raise exceptions. An agent must catch exceptions separately from checking result.error fields, complicating error handling logic and making the tool surface inconsistent.
- **fix**: Define error payload types for each tool (e.g., GraphFocusErrorPayload, GraphSummaryErrorPayload) and return them instead of raising. Validate parameters before calling internal logic and return error payloads for user-facing validation (depth < 0, top_n <= 0) similar to archy_contracts and archy_diff.
- **notes**: Key scope: All six exception-raising tools use ValueError or LayerConfigError for parameter validation (not infrastructure failures). This suggests they should follow the same pattern as archy_contracts, archy_diff, archy_dsm which return error payloads. HotspotsPayload and WhatToRefactorPayload demonstrate the codebase is aware of the note-field pattern for non-fatal errors (when git unavailable). The parameter validation exceptions violate this established pattern. CheckPayload is particularly problematic: it's a user-facing validation tool but raises LayerConfigError instead of returning a payload with error field.

### [major] No validation for max_nodes parameter allows negative or unbounded values  (confirmed)
- **loc**: src/archy/mcp.py:762-771 (tool definition) and 1294-1313 (_run_graph_dump)
- **claim**: The archy_graph tool accepts max_nodes with no validation. An agent can pass negative values, zero, or very large values (e.g., 999999), causing unbounded graph serialization.
- **evidence**: Line 765: max_nodes: int = 500 (no type constraint or validator). Line 1302: if node_count > max_nodes - this check allows negative max_nodes (all graphs pass) and doesn't prevent huge positive values. If agent calls archy_graph(path, max_nodes=999999) on a 500-node project, the graph serializes completely into JSON potentially gigabytes in size.
- **impact**: Agent can trigger unbounded JSON serialization by passing large max_nodes, bloating MCP response and consuming agent context. Negative max_nodes silently bypasses the size check (node_count > -1 is always true for non-empty graphs, so GraphTooLargePayload is always returned, which is actually safe but confusing).
- **fix**: Validate max_nodes > 0 and ideally set a server-side cap (e.g., max_nodes must be in range [1, 5000]). Return GraphTooLargePayload or raise ValueError if max_nodes is out of bounds.
- **notes**: The ticket should note: (1) Recommend server-side validation: max_nodes must be > 0 and <= some reasonable cap (e.g., 5000); (2) negative max_nodes should raise ValueError, not silently pass through; (3) huge positive values should be capped to prevent unbounded serialization; (4) consider making this consistent with other parameter validation patterns in the file (e.g., _run_hotspots at line 1358 validates top > 0). The fix is straightforward: add validation at the start of _run_graph_dump similar to line 1358-1359.

### [major] archy_trend has no validation on last_n, allowing unbounded history output  (confirmed)
- **loc**: src/archy/mcp.py:569 (tool definition) and 1138-1164 (_run_trend)
- **claim**: The archy_trend tool accepts last_n with no validation. An agent can pass last_n=999999 to read the entire history file, returning potentially thousands of TrendRow objects.
- **evidence**: Line 569: def archy_trend(path: str, last_n: int = 10) - no validation. Line 1140: window = rows[-last_n:] if last_n > 0 else rows - Python list slicing is safe (doesn't error on out-of-range indices), but returns all rows if last_n is very large. If history.jsonl has 10k rows * ~300 bytes per TrendRow, agent receives 3MB of data.
- **impact**: Agent can trigger unbounded history retrieval by passing large last_n values, bloating MCP response and agent context. While not a correctness bug, it's a resource exhaustion vector.
- **fix**: Validate last_n >= 1 and set a server-side cap (e.g., last_n must be in range [1, 1000]). Document the limit in the tool description.
- **notes**: 
1. VALIDATION MISSING: No Pydantic Field constraints on last_n parameter in the tool definition (line 569). Compare to other MCP tools in the same file that use DEFAULT constants (e.g., archy_impact, archy_affected).

2. SLICING BEHAVIOR: The implementation correctly uses Python's safe negative slicing (line 1140), so this is not a correctness bug or out-of-bounds error. However, slicing safely returning all rows is NOT the same as preventing unbounded output.

3. REAL RESOURCE IMPACT: With typical HistoryRow size (~437 bytes), a 10k-row history file would produce ~4.2MB JSON response, a 100k-row file would produce ~42MB. While not "unbounded" in the strict sense (bounded by disk), it exceeds reasonable agent context budgets.

4. RECOMMENDED FIX: Add Pydantic Field constraint with min=1, max=1000 to the last_n parameter, matching the pattern used elsewhere in the codebase (DEFAULT_MAX_CHAINS=20, DEFAULT_DEPTH=5). Update tool description to document the limit. This prevents accidental or malicious context bloat.

5. SCOPE NOTE: The finding's language "unbounded history output" is slightly imprecise—it's bounded by actual file size, not mathematically unbounded. However, the practical concern (agent requesting entire history without restraint) is valid and confirmed.


### [major] archy_check raises LayerConfigError unhandled, no error payload wrapper  (confirmed)
- **loc**: src/archy/mcp.py:533-537 (tool definition) and 977-1001 (_run_check)
- **claim**: If archy_check is called without a config_path and no archy.yaml is found near the project root, the tool raises LayerConfigError instead of returning a structured error payload.
- **evidence**: Lines 979-983: if discovered is None: raise LayerConfigError(...). Unlike archy_contracts and archy_diff which return error payloads, archy_check raises an exception that will serialize as an error to the agent without a consistent shape.
- **impact**: Agent expecting a CheckPayload receives an exception instead, complicating error handling. The error is valid (no config), but the error format is inconsistent with other tools like archy_contracts.
- **fix**: Return CheckPayload with an error field instead of raising, or wrap the raise in a try-except at the tool level and return a CheckPayload with error=str(exc). Keep error handling consistent across tools.
- **notes**: The finding is accurate. This is an inconsistency in error handling across MCP tools:

1. **Root cause**: _run_check() does not wrap its config discovery logic in error handling like _run_contracts() does (see lines 1011-1016).

2. **Current behavior**: Raises LayerConfigError at line 981, which FastMCP serializes as a JSON-RPC error response with error type -32603 (Internal error).

3. **Expected behavior**: Should return CheckPayload with either:
   - Option A: Add an error field to CheckPayload (like ContractsPayload)
   - Option B: Wrap the config discovery in try/except and return a CheckPayload with error information (like _run_diff which returns DiffErrorPayload on error)

4. **Impact**: Agents consuming these tools see inconsistent response formats. Some tools return structured payloads with error fields, while archy_check causes tool execution errors instead.

5. **Scope to capture**: This may extend to other tools - check whether all config-dependent tools (_run_snapshot, _run_record_baseline, _run_score, etc.) have consistent error handling patterns or if some also raise exceptions rather than returning structured errors.

### [minor] summarize_diff can raise exception during model_copy, no fallback to DiffErrorPayload  (confirmed)
- **loc**: src/archy/mcp.py:1086-1096, specifically line 1096
- **claim**: archy_diff calls summarize_diff inside model_copy without exception handling. If summarize_diff raises (e.g., due to OOM on huge graph or a bug in diff_summary logic), the exception propagates unhandled.
- **evidence**: Line 1096: return report.model_copy(update={"summary": summarize_diff(report, graph)}). If summarize_diff raises an exception, it is not caught. Unlike archy_contracts which returns ContractsPayload(..., error=str(exc)), archy_diff has no fallback.
- **impact**: Rare but possible: if summarize_diff fails, the tool raises an exception instead of returning DiffErrorPayload. Agent receives an unstructured error instead of the expected DiffReport | DiffErrorPayload union.
- **fix**: Wrap the summarize_diff call in try-except and return DiffErrorPayload(error=str(exc)) on exception. This makes error handling consistent with archy_contracts.
- **notes**: The severity is "minor" because summarize_diff failures are rare in practice (it's a relatively simple ranking/formatting function). However, this is a real contract violation: (1) the declared return type is `DiffReport | DiffErrorPayload`, but (2) an unhandled exception can escape, breaking the contract. Recommended fix: wrap the `summarize_diff()` call in try-except and return `DiffErrorPayload(error=str(exc))` on exception, matching the pattern used in `_run_contracts()`. This would make error handling consistent across MCP tools and ensure the declared return type is always honored. The impact is only manifest if summarize_diff itself raises (e.g., internal bug in compute_edit_risk on extreme graphs, or OOM on very large projects), but when it does occur, the agent receives an unstructured error instead of the expected payload format.

## code / simulate+refactor


### [minor] Unresolved module names are not deduplicated within a single call  (confirmed)
- **loc**: src/archy/simulate.py:159-233 (_resolve_delta function)
- **claim**: When a single module name fails to resolve multiple times (e.g., appearing in multiple edge specs), the same unresolved name is appended to the list multiple times, resulting in duplicated entries in the returned AppliedDelta.unresolved tuple
- **evidence**: Lines 196-197 append to unresolved list without deduplication. Example: add=[('bad1', 'b'), ('bad1', 'c')] results in unresolved=['bad1', 'bad1'] instead of ['bad1']. The list is converted to tuple at line 229 without dedup.
- **impact**: The user receives a misleading echoed report showing 'bad1' twice in the unresolved list when it appears only once as a failed resolution. This doesn't affect correctness of edge application (unresolved edges are skipped), but the diagnostic feedback is inaccurate.
- **fix**: Deduplicate unresolved names before returning from _resolve_delta, or convert to set and back to list at lines 204-207 where the final unresolved tuple is assembled
- **notes**: The issue is confirmed. Unresolved module names are not deduplicated within a single _resolve_delta call, resulting in duplicate entries in the AppliedDelta.unresolved tuple when the same module name fails to resolve multiple times across different edge specifications. This affects only the diagnostic feedback echoed back to the user, not the functional correctness of edge application (unresolved edges are properly skipped at lines 199-200). The fix should deduplicate unresolved names before returning, either by using set deduplication (e.g., `unresolved=tuple(dict.fromkeys(add_unres + rem_unres))` at line 207) or by applying similar deduplication logic as used for pairs at line 202.

### [nit] Synthetic edges lack source line numbers, creating incomplete violation reports  (confirmed)
- **loc**: src/archy/simulate.py:130
- **claim**: Synthetic edges added during simulation are created with lines=() (empty tuple). When these edges create new layer violations or SDP violations, the violations are reported with empty line numbers, which is correct for a synthetic edge but may confuse users expecting to see source locations
- **evidence**: Line 130: hypo.add_edge(edge.source, edge.target, kinds=('import',), lines=()). When find_violations or find_sdp_violations runs on hypo, it extracts lines from edge data (layers.py:165, 197). The synthetic edge will produce violations with lines=() tuple, making it impossible to point users to the source file where a violation was introduced.
- **impact**: User sees a violation report with empty lines field, limiting ability to understand where the hypothetical violation would appear in code. For simulation purposes this is acceptable (it's a hypothetical preview), but the user must understand that empty lines means 'synthetic edge'.
- **fix**: Document this behavior clearly in the simulate module docstring (already partly done at lines 14-18). Alternatively, store a synthetic line marker or attach metadata indicating 'this edge was synthetic'. The current approach is correct for the oracle validation (since synthetic and real edges should match on identity), but worth noting.
- **notes**: This is not a bug or missing feature - it's intentional design validated by the oracle test suite. The empty lines ARE correct for a synthetic edge, and users see that in the violation payload (lines field will be empty tuple). The code already documents this in simulate.py:14-18 docstring explaining the oracle safety. If any action is desired, it would be DOCUMENTATION-ONLY: perhaps add a note to the tool description in mcp.py or a user-facing tool doc clarifying that synthetic edges have empty line tuples (they're hypothetical). The current approach is architecturally sound - the oracle tests at test_simulate.py:168-201 confirm simulate(delta) == diff(written delta) across all reported fields despite the empty lines, proving the design is correct.

## research / AXIS_REVIEW


### [major] Selective example citation: msgspec labeled 'well-architected' despite lowest benchmark score  (confirmed)
- **loc**: AXIS_REVIEW.md:81, 86
- **claim**: msgspec is cited as one of the 'widely-respected, well-architected codebases' at the bottom of the calls_per_edge distribution, yet it scores 0.397 overall—the lowest in the entire 28-project benchmark, with 90% of code in cycles (acyclicity=0.100)
- **evidence**: Line 81 lists msgspec alongside starlette, scrapy, flask, boto3 as examples of 'well-architected' low-calls_per_edge projects. bench/results.md shows msgspec at 0.397 overall, 0.100 acyclicity, versus boto3 (0.653), starlette (0.613), and scrapy (0.603). msgspec is notably absent from the directionality argument section itself, appearing only in the distribution table (line 62) and shape-explanation (line 86).
- **impact**: The claim that 'both top and bottom [of calls_per_edge] are full of widely-respected, well-architected codebases' loses credibility when the bottom includes a project archy's own metrics rate as significantly below-average. Users reading the justification for rejection would not know msgspec's actual quality profile contradicts the claim.
- **fix**: Either (a) remove msgspec from the 'well-architected' characterization and acknowledge the bottom includes lower-quality projects, or (b) explicitly explain why msgspec's poor archy score should not count against the 'both are good' argument (e.g., if archy's acyclicity metric is known to misfire on certain patterns). Option (a) would require reconsidering the directionality claim with accurate examples.
- **notes**: The finding misdescribes msgspec's presence (claims it's "absent from the directionality argument section" when it appears at line 81), but the core issue is real. Line 81 of AXIS_REVIEW.md lumps msgspec in with starlette, scrapy, flask, and boto3 as examples of well-architected code at the bottom of the calls_per_edge distribution. However, msgspec's archy profile is dramatically worse: 0.397 overall (lowest of 28), 0.100 acyclicity (tied for lowest). The other four projects score 0.517-0.653 and have much higher acyclicity. This selective citation weakens the directionality argument's rhetorical force—the claim that the bottom is "full of widely-respected, well-architected codebases" becomes harder to defend when one of the cited examples is the worst-scoring project in the entire benchmark. The paper's own conclusion (section on discriminant validity) acknowledges that discriminant validity is weak and not empirically tested, which makes this citation problem more acute. Suggested fixes: (1) remove msgspec from the list and note the bottom includes lower-quality outliers, (2) explicitly address why msgspec's poor score doesn't undermine the directionality claim, or (3) use different examples (boto3, starlette, scrapy, flask are all legitimate examples without msgspec).

### [major] Double standard in directionality argument: cc_mean vs calls_per_edge receive asymmetric treatment  (confirmed)
- **loc**: AXIS_REVIEW.md:96, compared to lines 79-94
- **claim**: cc_mean's high values are justified as 'arguably could refactor toward less branching... even though codebases work,' establishing a directional signal. calls_per_edge's high values are dismissed as merely 'shape-driven' with no defensible direction, despite both involving codebases that 'work fine'
- **evidence**: Line 96 states: cc_mean top projects 'arguably could refactor toward less branching per function, even though those codebases work' (msgspec 5.33, ansible 4.42, datasette 4.37), while bottom 'is unambiguously good: short functions, few branches.' For calls_per_edge (lines 79-94), the same rhetorical move—'codebases work at both ends'—leads to the opposite conclusion: 'no defensible cross-population direction.' The distinction appears to turn on cc_mean having a documented literature basis (McCabe bug-density relationship) while calls_per_edge is 'shape-driven,' but this is not explicitly stated as the differentiating criterion.
- **impact**: The OECD four-criterion framework is presented as objective, but the application to directionality appears motivated by whether the signal fits the desired outcome. If the justification for cc_mean's directionality (literature + quality reasoning) were fully articulated upfront, the different treatment would be transparent. Instead, readers may infer the rejection of calls_per_edge is post-hoc: the criterion ('is higher better?') is being shaped to fit the desired answer.
- **fix**: Explicitly state that directionality can rest on either (a) cross-population quality reasoning with literature backing (cc_mean: 'short functions reduce bugs, per McCabe et al.') or (b) shape-driven trade-offs with no clear winner (calls_per_edge: 'both patterns valid'). Then apply that framework consistently. The current text achieves consistency rhetorically but not logically.
- **notes**: The ticket should capture:

1. **The actual problem:** AXIS_REVIEW.md applies the OECD four-condition framework (lines 70-75) but does not systematically apply it to cc_mean in the same explicit way it does for calls_per_edge. The directionality of cc_mean is established via asymmetry assertion (line 96) and quality reasoning, with literature and refactoring support mentioned later (lines 109, 117), but never framed as "fulfilling conditions for axis promotion."

2. **Transparency improvement:** Adding a section that explicitly states "cc_mean passes the OECD four-condition test (independence [line 54], directionality [asymmetric quality], actionability [lines 98-109], discriminant validity [lines 111-117])" vs "calls_per_edge fails conditions 2 (shape-driven, not quality-driven), 3 (no standard refactorings), and arguably 4 (both ends equally respected)" would make the reasoning much clearer.

3. **The substantive distinction that should be made explicit:** The literature backing (McCabe correlation with bug density) is relevant to directionality only insofar as it supports the asymmetry claim—i.e., that lower is objectively better, not context-dependent. This warrants explicit articulation because it's the crux of why the same reasoning (both ends work fine) leads to opposite conclusions.

4. **Alternative framing risk:** As written, the text could create doubt about whether the directionality determination for cc_mean is driven by evidence or by prior commitment to adding it as an axis (since it's already promoted in v0.20 per line 3). Explicitly framing the four-condition test as the criterion removes this ambiguity.

5. **Scope of fix:** The issue affects lines 79-117 (the Condition 2 section). A remediation would add explicit subsections: "Condition 2 (cc_mean): asymmetric quality with literature backing" and "Condition 2 (calls_per_edge): parity due to shape, no asymmetry justified." Then apply the same structure to conditions 3 and 4.

### [minor] Unacknowledged correlation between calls_per_edge and overall quality score  (confirmed)
- **loc**: AXIS_REVIEW.md:79-94
- **claim**: The document asserts 'directionality is shape-driven, not quality-driven' and claims the top and bottom of calls_per_edge are equally well-architected, implying no directional signal exists
- **evidence**: High calls_per_edge projects (>5): mean overall score 0.569. Low calls_per_edge projects (<3): mean overall score 0.557. Pearson r(calls_per_edge, overall_score) = +0.372 across all 28 projects—a modest but non-zero positive correlation. This is stronger than the correlation that supposedly justified calls_per_edge's orthogonality check (max |r|=0.229 against individual axes), yet the document frames the relationship as having no directional signal.
- **impact**: The directionality rejection rests on the claim that high and low calls_per_edge equally contain well-architected projects. The actual data shows a weak but consistent trend favoring higher calls_per_edge, contradicting the 'equally valid shape' framing. This weakens the case for rejecting directionality as a criterion.
- **fix**: Acknowledge the modest positive correlation between calls_per_edge and overall quality (r=0.372). Clarify whether this is treated as (a) evidence the signal does have directional signal (undermining the rejection), (b) noise that happens to correlate with other axes (which would require explaining why it's independent yet correlates), or (c) a genuine shape effect that correlates with quality incidentally. The current framing avoids this directly.
- **notes**: 1. PRIMARY ISSUE: The document cites max |r| = 0.229 (line 54) but current bench/results.md shows 0.217. Verify which benchmark version was used for the orthogonality claims. The 8-day gap (2026-05-14 vs 2026-05-18) suggests the numbers may have shifted in a data refresh.

2. FRAMING GAP (not a factual error): The document correctly argues that calls_per_edge is "shape-driven" and both high/low groups contain good projects, but it does not explicitly address the r=0.385 correlation with overall_score or explain why this modest but measurable positive correlation does not constitute a directional quality signal. The four-condition OECD framework is sound (independence ≠ quality signal), but the document would benefit from explicitly stating: "calls_per_edge correlates weakly with overall score (r=0.385), but this is likely confounded with architectural shape, not independent architectural quality."

3. MSGSPEC CONSIDERATION: The low group's mean (0.557) is significantly pulled down by msgspec's 0.397 score—an outlier that the document doesn't flag. Excluding msgspec, the low group mean rises to ~0.597, nearly identical to the high group's 0.598. This actually strengthens the document's "no directional signal" argument for quality, but obscures the fact when reported.

4. The finding's claim is valid but slightly overstates the conflict: the document's argument is that the signal is orthogonal to quality because it's driven by shape, not because high and low groups are numerically identical. The correlation exists but is spurious.


### [minor] Discriminant validity criterion applied unevenly: hypothetical expert judgment used to reject calls_per_edge but not type-hint coverage  (confirmed)
- **loc**: AXIS_REVIEW.md:111-118, 157-159
- **claim**: Discriminant validity is invoked to reject calls_per_edge based on a hypothetical '10-expert ranking study' that 'has not been done,' while type-hint coverage is initially endorsed based on speculative community perception ('heavily-typed projects tend to rank high')
- **evidence**: Lines 113-115: 'The study has not been done... the structural argument is: the bench top and bottom both contain widely-respected codebases.' Lines 157-158: 'Likely discriminant validity: heavily-typed projects... tend to rank high on community quality perceptions.' Both rest on unvalidated assumptions, yet one supports rejection and the other supports recommendation. Later, TYPE_HINT_COVERAGE_EMPIRICS.md empirically rejects type-hint coverage anyway—on different grounds (value-prop, niche)—suggesting discriminant validity was not the real blocking criterion.
- **impact**: The OECD framework appears applied inconsistently. If a hypothetical expert ranking can reject calls_per_edge, it should also reject type-hint coverage. If community perception can support type-hint coverage, it should also support calls_per_edge. The asymmetry suggests other factors drove the decisions.
- **fix**: Either apply discriminant-validity checks uniformly (defer to empirical study for all candidates, or use uniform standards for speculation), or acknowledge that discriminant validity is a secondary criterion, with directionality or value-prop being the primary blocker.
- **notes**: The asymmetry is real but not consequential to final outcomes (both metrics were rejected from the score). However, the documents should clarify:

1. In AXIS_REVIEW.md lines 157-158, the phrase "Likely discriminant validity" should either (a) be marked as speculative with a note that this will be empirically tested, or (b) acknowledge that calls_per_edge's discriminant validity is also speculative and the endorsement is based on the OTHER dimensions where type-hint coverage is stronger (directionality=unambiguous vs contested, actionability=strong vs weak).

2. The comparison in line 159 ("structurally stronger than calls_per_edge on every dimension") is accurate only if directionality and actionability are counted - discriminant validity is actually contested/weak for both. This could be clarified.

3. TYPE_HINT_COVERAGE_EMPIRICS.md could explicitly note in lines 72-80 that this discriminant-validity failure had already been acknowledged as a weakness of calls_per_edge in AXIS_REVIEW.md, making this particular failure-mode not surprising in hindsight.

The underlying issue is not "wrong decision" but "inconsistent reasoning process" - the documents applied different evidentiary standards to two candidates that ended up failing on the same criterion.

## research / CALL_WEIGHTED_Q


### [major] Narratives for 'moved up' and 'moved down' patterns don't correlate with calls/edge distribution  (confirmed)
- **loc**: CALL_WEIGHTED_Q_EMPIRICS.md, lines 57-75; bench/call_weighted_modularity_results.md, lines 44-70
- **claim**: Document claims projects 'moved up' have 'dense intra-community dispatch' and 'moved down' projects have calls that 'cross community boundaries.' The narratives frame calls/edge as explanatory for why projects move up or down.
- **evidence**: Projects moved UP (delta > 0) have mean calls/edge = 4.75; projects moved DOWN (delta < 0) have mean calls/edge = 16.98. However, this reverses the narratives' implication: if calls-per-edge explained the pattern, high calls/edge should predict moving UP (amplifying structure), not DOWN. Specifically: archy (2.74) and rich (2.75) moved UP; httpx (4.31) and msgspec (2.67) moved DOWN. The calls/edge metric does not distinguish the groups. The pattern-direction is determined by the SIGN of delta (whether weighting increases or decreases Q), not by calls/edge magnitude.
- **impact**: Readers are given post-hoc narratives that appear to explain empirical differences but are actually confabulated. The real driver of rank movement is heterogeneity in how the greedy algorithm partitions under weights, not call density. This misdirects interpretation of what the gap signal actually means.
- **fix**: Remove the calls/edge column from the 'Moved up' and 'Moved down' tables, or add a column showing which edges actually carry the most traffic to verify the claims. Alternatively, run a case-study analysis on 2-3 projects showing which actual call edges shift community membership under weighting.
- **notes**: The issue is confirmed but the scope should be clarified: 

1. LOCATION ACCURACY: The finding cites lines 57-75 of CALL_WEIGHTED_Q_EMPIRICS.md. The actual file is at `/Users/hosanglee/archy/docs/research/CALL_WEIGHTED_Q_EMPIRICS.md` (the finding was missing the full path). The "Moved up" table is at lines 59-65; the "Moved down" table is at lines 69-75.

2. IMPACT SCOPE: The tables appear in a research document, not in user-facing CLI output. The metric is intentionally shipped as a parallel diagnostic gap signal (not a directional axis), as documented in the "Decision" section and "Why a parallel diagnostic" section.

3. PRESENTATION WEAKNESS: The tables present narratives without quantitative support for the claimed intra- vs. cross-boundary distinction. A reader could misinterpret calls/edge as a predictor of rank movement. The suggestion to add a column showing which edges actually carry the most traffic would be valuable for transparency.

4. ACTIONABLE FIX: Either: (a) Remove the calls/edge column from these explanatory tables and move the narratives to the body text with better explanation of what drives the rank shifts, or (b) Add a footnote explaining that calls/edge is a project-wide average and doesn't distinguish intra- vs. cross-boundary calls, then cite specific high-traffic edges in each narrative.

5. DOCUMENT INCONSISTENCY: The document correctly identifies the directionality issue in section "Why the empirics don't justify replacing unweighted Q" but this insight isn't reflected in the narrative tables themselves. Integrating this context into the table captions or footnotes would improve clarity.

### [major] numpy and sqlalchemy's architectural quality asserted without evidence  (confirmed)
- **loc**: CALL_WEIGHTED_Q_EMPIRICS.md, line 95
- **claim**: Document claims numpy's drop from rank 10 to 22 'reflects its small-core / broad-call shape, which is *intentional* and widely considered well-architected.' Same for sqlalchemy. These claims are presented as justification for why penalizing their designs with weighted-Q is 'not defensible.'
- **evidence**: The document provides no citations, no expert rankings, no empirical validation that numpy's architecture is 'widely considered well-architected' specifically because of its small-core / broad-call design. AXIS_REVIEW.md (line 81) makes a similar claim ('both top and bottom are full of widely-respected, well-architected codebases') but also without evidence. The document itself explicitly defers the real test: 'The 10-expert ranking study from AXIS_REVIEW.md remains the cleanest way to resolve direction-contested signals. Out of scope here.'
- **impact**: A core argument for shipping-as-diagnostic-only rather than axis-replacement rests on an unvalidated premise that experts would endorse these designs. If expert ranking studies (when conducted) show numpy or sqlalchemy's architectures are actually considered suboptimal despite being well-known, the entire directionality argument collapses. The document makes a strong claim about contested directionality while admitting the required study is out of scope.
- **fix**: Either conduct or cite the 10-expert ranking study before making claims about what experts would 'widely consider well-architected.' Or downgrade the claim to 'these projects are mature and in active use; we lack evidence either way on expert preference for their call-graph layouts.'
- **notes**: The ticket correctly identifies that CALL_WEIGHTED_Q_EMPIRICS.md and AXIS_REVIEW.md make strong claims about "widely considered well-architected" designs without empirical support. Key scope for the ticket: (1) The phrase "widely considered" appears in both documents but is unsupported assertion, not evidence. (2) The document itself defers the validating study (10-expert ranking) to "out of scope." (3) This creates a logic loop: a design decision (shipping call-weighted-Q as diagnostic-only) rests on an unvalidated premise about expert opinion. (4) The document does acknowledge this is a gap ("the cleanest way to resolve direction-contested signals... remains out of scope"), but ships the decision anyway. (5) Actionable remedies: (a) Conduct the deferred expert ranking study before asserting what experts would conclude, OR (b) Downgrade the claims to "these are mature projects with intentional designs" without the "widely considered well-architected" framing that presupposes expert validation. The actual empirical fact (numpy drops from rank 10 to 22) is uncontested; what's missing is the justification for why that's acceptable.

### [major] The five use cases don't actually require gap magnitude, only direction or presence  (confirmed)
- **loc**: CALL_WEIGHTED_Q_EMPIRICS.md, lines 26-34
- **claim**: Document claims all five use cases 'require the *gap* between the two values rather than either value alone.' The gap magnitude and interpretation brackets (line 16-20) are presented as essential to each use case.
- **evidence**: Use case 1 (mismatch detector): Asks 'Q_weighted < Q_unweighted?' This only requires direction (sign), not magnitude. Both numpy (-0.055) and msgspec (-0.214) signal mismatch; magnitude is irrelevant to the clinical meaning. Use case 2 (drift detection): Requires CHANGE in gap over time (Δgap), not the gap itself. Use case 3 (layer validation): Asks 'is the gap large?' But 'large' is undefined and the ±0.05 brackets don't correlate with actual mismatch severity (httpx is -0.010 yet ranks drop 13). Use case 4 (agent feedback): Could report just 'calls cross boundary' or 'calls align with structure' without gap magnitude. Use case 5 (cross-codebase comparison): Could compare gap DIRECTION, not magnitude (direction is more robust to weighting-policy choices).
- **impact**: The document oversells what the gap provides. If the real value is directional (does the sign match intuition?), then much of the statistical machinery (correlation analyses, rank tables) is overfitting the signal. This risks presenting noise as signal and makes it harder to understand what's actually load-bearing.
- **fix**: Reframe the diagnostic as 'directional gap signal' (amplify vs. cross, not magnitude-based buckets), or explicitly test gap-magnitude against the five use-case outcomes. If use cases only need direction, simplify the output to avoid the illusion of precision.
- **notes**: The ticket should capture that: (1) Use Case 2's discussion of drift detection is incomplete—it needs explicit guidance on measuring Δgap over time in history.jsonl or similar, not just "widening gap"; (2) Use Cases 3 and 5 should be reframed to acknowledge that gap DIRECTION (sign) is the primary signal, not magnitude; (3) The ±0.05 thresholds should be revalidated against real outcomes (e.g., whether gap magnitude predicts architectural degradation severity) or reframed as arbitrary categorization buckets with no special significance; (4) The five use cases should be audited: Use Case 1 (mismatch), 4 (agent feedback) are directional only; Use Case 2 requires Δgap not gap itself; Use Cases 3-5 may not need magnitude. A reformulation to "directional gap signal" as the finding suggests may be warranted for Use Cases 1, 4, 5, while Use Case 3 (layer validation) needs empirical grounding for what "large" means in terms of structural consequences. The current design is appropriate (showing both numbers and prose), but the CLAIMS in the document should be more precise.

### [minor] No validation that gap is robust to variations in weighting policy  (confirmed)
- **loc**: bench/call_weighted_modularity.py, lines 77-101; CALL_WEIGHTED_Q_EMPIRICS.md, line 43
- **claim**: Document claims the weight=1 fallback is the correct policy because alternatives (weight=0 or missing-as-zero) collapse plugin/registry shapes. But only one weighting scheme is tested.
- **evidence**: The script mentions two alternatives were 'considered and rejected' (lines 77-101) but no empirical comparison is provided. The document does not show results under weight=0 (all non-call edges removed) vs. weight=1 (fallback). Without this comparison, readers cannot judge whether the final results are robust or artifacts of the chosen policy. Additionally, other policies (e.g., weight = min(1, call_count) to cap outliers like numpy's 52.68 calls/edge) are not explored.
- **impact**: The gap values (especially for projects with extreme calls/edge like numpy 52.68) may be sensitive to the weighting policy. If a different weighting scheme produces different rank orderings, the interpretation of which projects 'amplify' vs. 'cross' community structure becomes policy-dependent, not empirically grounded.
- **fix**: Run the bench under 2-3 weighting policies (weight=1 fallback, weight=0 for import-only, weight=tanh(call_count) to dampen outliers) and report rank-shift correlation between policies. If gap interpretation is robust, it should survive reasonable policy variations.
- **notes**: The ticket should capture that while the weight=1 fallback choice is reasonable and the single-policy empirics are thorough (27 projects, rank shifts, orthogonality checks), the decision lacks comparative robustness analysis. Recommended action: Run bench under 2-3 alternative weighting schemes (weight=0 for import-only, weight=1 fallback [current], weight=tanh(call_count) to dampen outliers like numpy's 52.68 calls/edge) and report rank-shift correlation or Spearman's rho between the rank orderings under different policies. If rank correlations are high (>0.9), the gap interpretation is robust. If low, the findings are policy-dependent. This is a quality/rigor issue for a research document, not a correctness bug in the shipped code. The parallel diagnostic is shipped correctly with weight=1; the research claim just needs empirical backup.

### [minor] Claim that calls explain numpy's rank movement is post-hoc and confounds multiple factors  (confirmed)
- **loc**: CALL_WEIGHTED_Q_EMPIRICS.md, lines 73-76
- **claim**: Document claims numpy 'calls cross every boundary' and moves from rank 10 to 22. The table justifies this with 'calls/edge = 52.68' (the highest in the dataset) and a narrative about 'small-core, broad call surface.'
- **evidence**: numpy has 635 modules and 2723 edges, making it the 6th-largest project by module count. When the greedy algorithm is rerun with call-weighted edges, the partition changes—but the document provides no analysis of which edges shift communities. It's possible the rank movement is driven not by calls crossing boundaries within the original unweighted communities, but by (1) the algorithm finding a completely different partition, or (2) other projects' deltas shifting their ranks, affecting numpy's relative position. The fact that numpy also has 'famously small-core' (per the narrative) is a qualitative claim about architecture, not an empirical finding from the call-weighted Q computation.
- **impact**: The narrative anchors the rank movement to architectural intent ('famously small-core, broad call surface') without demonstrating that weighting actually shifts the partition in a way consistent with that narrative. This conflates codebase shape (which is true) with the metric's ability to detect it (which is not validated).
- **fix**: For numpy (and 1-2 other large rank-movers), show: (1) which communities existed in the unweighted partition, (2) which communities exist in the weighted partition, (3) which call edges actually switched communities, (4) do those edges match the narrative? This would ground the post-hoc explanation in data.
- **notes**: CONFIRMED with nuance: The finding correctly identifies that the document makes a causal claim ("numpy's rank drop reflects its small-core/broad-call shape") without showing the partition-level evidence to support it. The document provides: (1) the rank shift fact (10→22), (2) the calls/edge density (52.68), (3) a narrative interpretation ("calls cross every boundary"). What it does NOT provide: (1) the unweighted partition for numpy, (2) the weighted partition for numpy, (3) which edges moved between communities, (4) quantification that the boundary-crossing edges match the "small-core, broad call surface" narrative. The cited reference file `research/product/call-weighted-q-analysis.md` does not exist in the repo. The document is somewhat transparent about being interpretive (calling them "readings" in informal table prose rather than "empirical findings"), but it shifts to causal language in section 89-95 ("numpy's drop... reflects...") without disclosing the analytical gap. The confounding factor concern is valid: numpy's rank is partly determined by (a) its own Q delta (-0.055, second-worst in dataset) and (b) other projects' shifts reshuffling the global rank order (msgspec moves 24 positions, mkdocs moves 13 up). Severity is appropriately "minor" — this is methodological transparency/rigor gap, not a factual error or major omission that breaks the paper's conclusions. The paper's decision to ship call-weighted Q as a diagnostic (not axis replacement) stands on sound reasoning even without this partition analysis.

## research / DSM_EMPIRICS


### [major] Feedback rejection conflates 'different normalization' with 'equivalent signal' without demonstrating equivalence  (confirmed)
- **loc**: docs/research/DSM_EMPIRICS.md, lines 77-85
- **claim**: Feedback is rejected as 'a different normalization of the same underlying property' as acyclicity. The document argues there's 'no clear story for why the new normalization is better.' But this dismisses the possibility that the gap between feedback and acyclicity is itself a meaningful signal, parallel to how call-weighted Q is treated.
- **evidence**: The parallel case: CALL_WEIGHTED_Q_EMPIRICS.md explicitly endorses call-weighted Q as a DIAGNOSTIC shipped alongside unweighted Q because 'the gap between weighted and unweighted Q is the load-bearing signal.' The pairwise correlation between feedback and acyclicity is not reported in the document, making it impossible to assess whether they are actually redundant (high r) or distinct (low r). The document reports acyclicity = r(-0.688) with feedback, meaning feedback could be measuring feedback-fraction specifically, not tangle-ratio.
- **impact**: A potentially useful diagnostic signal (the gap between feedback-fraction and tangle-ratio) is rejected without exploring whether it captures something acyclicity does not. The decision to ship call-weighted Q as a diagnostic sets a precedent that should have triggered exploration of feedback-as-diagnostic, not just feedback-as-axis.
- **fix**: Either (1) compute and report the pairwise r(feedback, acyclicity) to directly test redundancy, or (2) ship feedback as a parallel diagnostic similar to weighted Q, exploring whether the gap tells a story about the type of cycles present (e.g., 'many small cycles' vs 'deep chains').
- **notes**: 
CRITICAL DATA ISSUE: The document claims "Pairwise these two are also distinct (r = -0.581)" for feedback vs acyclicity, but this pairwise correlation is NOT computed or reported in bench/dsm_results.md. The pairwise table only shows correlations among the four DSM signals themselves. The -0.581 appears elsewhere as r(modularity, depth) in other studies. Need to either:
1. Verify if the pairwise r(feedback, acyclicity) = -0.581 value is correct (should be computed and reported)
2. Clarify the source of this number if it's from a different dataset

SUBSTANTIVE CONCERN: The decision to reject feedback-as-diagnostic while shipping call-weighted-Q-as-diagnostic lacks explicit justification for why the gap concept doesn't apply similarly. Both pairs are negatively correlated (distinct signals). The document should either:
1. Explicitly explore whether feedback-acyclicity gap tells a meaningful story about cycle structure (small-cycle-heavy vs large-cycle patterns)
2. Or explain why the gap concept fundamentally doesn't work for this pair while it does for modularity-Q

The document correctly ships the DSM visualization (which provides positional data) but may be prematurely rejecting feedback-as-parallel-diagnostic without exploring the gap interpretation.


### [minor] Only four DSM candidates tested; no justification for why these were the strongest  (confirmed)
- **loc**: docs/research/DSM_EMPIRICS.md, lines 36-47 ('The four candidates') and overall framing
- **claim**: The document tests exactly four DSM-derived scalars (feedback, bandwidth, block_comm, block_layer) without explaining why these were chosen or whether they represent the strongest candidates from DSM literature (Steward 1981, Eppinger & Browning, MacCormack 2006).
- **evidence**: The DSM literature referenced (Steward 1981, MacCormack 2006, Eppinger & Browning) defines classic metrics like sequenciability, entropy/disorder, and modularity nestedness measures that do not appear in the bench script (bench/dsm.py). The document lists 'the four candidates' without stating this is exhaustive or that alternatives were evaluated and rejected. The bench script comments describe only these four without acknowledging other options.
- **impact**: Readers cannot assess whether the strongest DSM candidates were actually tested. If a classic measure from Steward or MacCormack literature was omitted and would have performed better, the empirical foundation for 'no DSM scalar ships' is weakened.
- **fix**: Either (1) explain the candidate-selection rationale (why these four over alternatives from the literature), or (2) test additional candidates from DSM literature (e.g., sequenciability, entropy measures) and document why they were excluded.
- **notes**: Ticket should capture: (1) DSM_EMPIRICS.md must either explain the candidate-selection rationale (e.g., "we evaluated N candidates and selected these 4 as representative/promising because...") or add a subsection describing evaluation of alternatives from Steward/MacCormack/Eppinger literature (e.g., sequenciability, entropy-based measures, tearing-based feedback arc set) and why they were excluded or not prioritized. (2) The bench/dsm.py docstring (lines 6-22) should align with whichever approach is chosen. (3) Consider cross-referencing RESEARCH_METRICS.md section B7 (DSM-derived feedback measure / tearing) and explaining the relationship to what was tested, since that section discusses FAS equivalence but bench.py only tests edge-fraction feedback. (4) This is a documentation/transparency issue with minor impact on readers' ability to assess robustness, not a technical flaw in the empirical study itself (the four tested are reasonable; the gap is explaining *why* they were picked).

### [minor] Bandwidth's 'conflation' of two properties is described but not empirically distinguished  (confirmed)
- **loc**: docs/research/DSM_EMPIRICS.md, lines 91-94
- **claim**: The document claims bandwidth 'conflates long-range coupling with small number of edges spread across many layers, which is a different property.' This is asserted without analysis of whether the two are actually entangled in the data or could be separated.
- **evidence**: The document defines bandwidth as 'mean |i-j|/N' and notes this could arise from either (a) few edges spanning far apart layers, or (b) many edges necessarily spreading across layers in a layered architecture. No analysis distinguishes these cases in the benchmark data. The claim that fastapi/starlette are 'not analogously shaped' to pygments acknowledges shape variation but doesn't decompose what bandwidth is measuring in each case.
- **impact**: The rejection of bandwidth rests partly on the claim that it conflates two properties, but without empirical decomposition, the claim is speculative. This weakens the OECD-criteria argument (specifically the actionability criterion).
- **fix**: For the three named projects (fastapi, starlette, pygments), decompose their bandwidth values by plotting dependency-distance distribution, or factorize bandwidth into edge-count and mean-distance components to test whether conflation is real.
- **notes**: The finding is valid but the scope and impact should be clarified: (1) The conflation claim is ASSERTED but UNVERIFIED - no decomposition of fastapi/starlette/pygments' bandwidth values distinguishes whether high values come from distance vs edge-count; (2) The missing analysis would strengthen criterion #2 (direction-inversion) but does not undermine the overall decision since criteria #1, #3, #4 independently reject bandwidth; (3) The suggested action (plot dependency-distance distribution per project, or decompose bandwidth into mean-distance × edge-count) is scientifically sound and would be valuable for future research completeness, though not necessary to defend the v0.3 decision. Consider this a documentation gap rather than a methodological flaw in the core decision.

## research / INLOOP_PREVALENCE


### [major] Unjustified numeric threshold (0.005) for score 'triviality' redefines away inconvenient data  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md §4 (Finding 3), lines 104-112
- **claim**: Document argues 98% of score drops are 'trivial' (<0.005 magnitude) and therefore score should be advisory not blocking. But the 0.005 threshold is asserted without justification—no reference to measurement error, domain expertise, or practical impact. It serves only to dismiss 310/315 score regressions as noise.
- **evidence**: Lines 106-109: 'Score drops are common (29%) but almost entirely **trivial in magnitude**: of the 315 commits that dropped, **98% dropped by less than 0.005**'. No justification for threshold provided anywhere in document. The data shows exactly 5 score drops >= 0.005 (1.6%), but this critical count is never discussed. Compare to cycles: also 5 events at ~0.5% rate. The symmetry is suspicious and unexamined.
- **impact**: This circular logic (define trivial as small, conclude small things are trivial) allows the document to dismiss score-based signals entirely. This is a foundation for the product thesis that cycles (not score) should gate decisions. But if the score metric is so broken it captures nothing meaningful, why does the corpus include it at all? The reasoning appears tailored to support pre-determined conclusions about which signals matter.
- **fix**: Either (1) justify the 0.005 threshold with reference to measurement noise, inter-rater reliability, or domain expertise, or (2) present the 5 large score drops separately as a distinct signal class alongside cycles, or (3) acknowledge that the score metric's composition and sensitivity are unmeasured, making it unsuitable for decision-making either way.
- **notes**: This finding is not a minor quibble. It identifies a load-bearing claim in a foundational research document that lacks justification per the project's own cited standards. The project's SCORING.md documentation explicitly cites Applied Sciences 2021 ("Techniques for Calculating Software Product Metrics Threshold Values") stating that thresholds must be derived empirically from a benchmark population, not asserted from intuition. The INLOOP_PREVALENCE_EMPIRICS.md violates this standard by asserting 0.005 without derivation.

The fix requires one of three paths (as the finding suggests):
1. **Justify 0.005 empirically**: Run measurement-error analysis, inter-rater reliability tests (if applicable), or domain-expert calibration to establish that scores < 0.005 fall within acceptable bounds.
2. **Treat the 5 large drops as a separate signal**: Present them alongside the cycle-introducing commits as a distinct class of "medium-risk score regressions" rather than lumping them under noise.
3. **Acknowledge uncertainty**: Explicitly state "the score metric's sensitivity is unmeasured; we cannot yet claim that sub-0.005 drops are meaningless."

The document's Q1b protocol section (line 163) already uses "beyond the 0.005 noise floor established in Finding 3" for future A/B testing of agent-in-the-loop, so this unjustified threshold now gates a causal claim about agent safety. Until justified, that gate is shaky.

Secondary concern: The unexamined symmetry (5 cycles, 5 large score drops, both ~0.5% rate) warrants investigation. One overlap (mkdocs) could indicate the two signals are correlated; if so, the independence assumption of treating them separately (cycles gate, score advisory) may not hold.

### [major] Agent-size claim extrapolated from external paper but inconsistent with corpus's own risk thresholds  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md lines 94-102
- **claim**: Document claims 'Agents push toward exactly the change regime where the per-change cycle rate is elevated' based on synthesis's claim agents are 154% larger. But 2.54x the human baseline (1 file) = ~2.54 files median, which this study bins as 'small' (1-3 files, 0.1% cycle rate), not the 'large' regime (10+ files, 4.5% rate) where elevated risk actually appears.
- **evidence**: Human commits in study: median 1 file. Synthesis claims agents 154% larger = 2.54x multiplier. 2.54 files falls in '1-3 file' bucket where cycle rate is 0.10% (1 of 958). The document's own size-risk analysis (lines 268-273) explicitly bins commits at <=3 vs >=10 and shows the cycle rate is low in the <=3 regime. Yet the inference jumps to 'agents are in the high-risk regime' without calculating where 2.54 actually falls.
- **impact**: The product thesis depends on agents introducing structural regressions at elevated rates. This inference chain is the only quantitative bridge from 'agents are bigger' to 'agents need archy.' The argument is hand-wavy: agents are bigger, bigger commits have more cycles (true, but not big enough per this study), therefore agents will have more cycles (unsupported). The inference would need agent size to exceed ~5-7 files median to enter the meaningful-risk zone, but 2.54 doesn't get there.
- **fix**: Either (1) provide an explicit calculation: 'Agent median size X implies they will be in commit size range Y, which has cycle rate Z%', or (2) acknowledge the 154% figure is coarse and may not transfer to this corpus, or (3) run a direct agent comparison arm (Q1b) to measure actual agent cycle rates instead of inferring from size alone.
- **notes**: 
1. SCOPE: The inference error is in lines 94-102 of INLOOP_PREVALENCE_EMPIRICS.md, specifically the claim that agents "push toward exactly the change regime where the per-change cycle rate is elevated."

2. REQUIRED FIXES (pick one):
   - Add explicit calculation: "If agents are 2.54× baseline (154% larger), they fall in the 1-3 file bucket with 0.1% cycle rate, NOT the elevated-risk regime. To enter meaningful-risk zone (3.26%+), agent median would need to reach ~4+ files."
   - OR qualify the 154% claim: "The 154% figure is from broader AI-PR literature and may not transfer to this corpus's size distribution. Direct Q1b measurement needed."
   - OR advance Q1b timeline: "We cannot infer agent risk from baseline size alone; Q1b direct agent comparison is required to validate the premise."

3. ASYMMETRY OBSERVATION: The document correctly shows cycle-introducing commits are 7x larger (7 files vs 1), but doesn't show that a 2.54x increase (to 2.54 files) reaches that threshold. Four of five cycle-introducing commits (3-16 files) are well above 2.54. This gap should be explicit.

4. INTEGRITY NOTE: The conclusion (lines 176-179) is actually well-hedged—it frames archy as a gate on "large transformative changes agents produce most and handle worst," without claiming the 154% baseline directly reaches that bar. The problem is in the bridge logic in lines 94-102, not the overall thesis.

5. NO HIDDEN BINNING FOUND: The finding's references to "0.1% cycle rate" and "4.5% rate" as study-defined bins are accurate (I found 0.10% and 4.55%, matching the finding's numbers). These are not from external sources; they emerge from binning the actual 1,072-commit dataset by file count.


### [major] Critical metric misalignment: largest cycle-introducing commit has positive score delta, unexamined  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md §2 and §3, implicitly
- **claim**: The requests commit 561e4b6889 (16 files, introducing an 8-module SCC, the largest cycle event in the study) has overall_score +0.01210, a positive delta. The document frames cycles as structural damage but this cycle co-occurred with score improvement. This contradicts the implicit premise that score and cycles are aligned signals.
- **evidence**: Raw data: psf/requests 561e4b6889: cycle_count_before=0, cycle_count_after=1, overall_delta=+0.01210, acyclicity_delta=-0.13333. The negative acyclicity component was overwhelmed by positive components elsewhere. This is the opposite of what Finding 3 claims: that score should be advisory because it's noisy. If score is noisy, why does the document pit 'rare-firing cycles' against 'noisy score' as if they're independent signals? They're not—they can move in opposite directions.
- **impact**: The document's logic rests on two adjacent claims: (1) cycles are the 'FP-free gate' (Finding 2), (2) score is too noisy to block on (Finding 3). But if score can improve while cycles worsen, score is not just 'noisy'—it's *misaligned* with the causal concern archy claims to detect. This undermines the entire premise that cycles and score measure the same dimension of structural health. It suggests archy's scoring model is broken or incomplete, not just jittery.
- **fix**: Investigate and report: what driven the score improvement in the requests commit? Decompose the score delta by component (acyclicity, modularity, density, coupling). Discuss whether score is a valid measure of 'structural health' at all, or if it measures something orthogonal to cycles. This will clarify whether Finding 3's dismissal of score is justified or whether it's an artifact of metric misalignment.
- **notes**: The ticket should capture:

1. MISSING ANALYSIS: The document needs to decompose the requests 561e4b6889 score delta by the five component axes (modularity, acyclicity, depth, equality, complexity) to show what actually drove the score improvement. Currently, only acyclicity_delta is reported; the other four component deltas are not disclosed.

2. DECOUPLING VS NOISINESS: Finding 3's framing conflates two different problems:
   - Noisiness: trivial magnitude changes (e.g., -0.00009)
   - Decoupling: opposite-direction movement between cycles and score
   
   The requests commit is NOT noisy (it's a top-1.4% score movement). It reveals genuine decoupling, which is a more serious design issue than noisiness.

3. SYSTEMATIC VS EDGE CASE: With only 5 cycle events in the study, determining whether score-cycle decoupling is systematic or rare requires:
   - Analysis of whether this 20% decoupling rate (1/5) is expected given the model structure
   - Whether it's specific to large commits (which simultaneously add modules for modularity improvement)
   - Whether score improvements during cycles happen more often in certain domains/codebases

4. GATE SAFETY: If score and cycles can move opposite, the document's conclusion that cycles are the "right gate" while score is "advisory" may be incomplete. An agent seeing both signals needs clear guidance on what to do when they conflict. The current framing assumes they won't materially conflict.

5. SCOPE FOR REMEDIATION: This affects sections 2-3 and the conclusion. A minimal fix is to acknowledge the requests exception and clarify that Finding 3 rests on an incomplete correlation (4/5 agreement). A more complete fix requires the component decomposition and systematic analysis mentioned above.

### [major] Exactly 5 commits exceed score drop threshold (0.005), same count as cycle regressions, never discussed  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md §3, implicitly; line 163 invokes this threshold
- **claim**: Document identifies 315 score regressions, of which 5 exceed -0.005 magnitude. There are also exactly 5 cycle regressions. The coincidence is never acknowledged or explained. The document treats score and cycles as distinct signals, but this 1:1 correspondence suggests they may be detecting overlapping or identical events.
- **evidence**: Score drops >0.005: httpx 89a8100b6c (-0.01968), starlette f985e49629 (-0.00829), mkdocs c5d3555594 (-0.00749), datasette 9ca860e54f (-0.00670), datasette b1289a73f9 (-0.00560). Cycles: requests 561e4b6889, httpx 3cbe7315e8, httpx 92fbe5fd87, scrapy ddea6b7bfa, mkdocs c5d3555594. Overlap: 1 (mkdocs). This means 4 score drops and 4 cycles are disjoint. The document does not explore whether the 'big score drops' are an alternative proxy for structural damage.
- **impact**: Finding 3 uses the 0.005 threshold to argue score should be advisory. But the 5 commitsthat exceed this threshold are structurally anomalous (either cycles or something else). If the document is using 0.005 to separate 'noise' from 'real events,' it should investigate whether those 5 real events have a common cause and whether that cause overlaps with cycles. The current presentation makes it appear that 98% of score drops are nothing, but 1.6% are something real—a classification that should be explained.
- **fix**: Analyze the 5 commits with score drop >0.005: four have no cycle regression. What structural feature do they share? Are they refactors, large features, or reorganizations? Do they have high `edit_risk`? This will clarify whether score is capturing a different dimension of structural concern or whether it's simply unreliable.
- **notes**: The ticket should capture:

1. DATA VERIFICATION: Confirmed exactly 5 score drops >0.005 and exactly 5 cycles, with 1 overlap. Data differs slightly from document claim (314 score drops, not 315; actual percentages are 1.6% exceeding threshold vs 2% implied).

2. MISSING ANALYSIS: The document identifies the 0.005 threshold as the "noise floor" in Finding 3 but never analyzes:
   - Why the 5 commits exceeding this threshold are important
   - Whether the 4 non-cycle score drops have a common cause
   - Whether the score metric is capturing something distinct from cycles

3. STRUCTURAL FEATURES OF THE 4 NON-CYCLE SCORE DROPS:
   - httpx 89a8100b6c: -0.01968 drop driven by acyclicity improvement (+0.10526), 3 files; appears to be a deletion/cleanup reducing tangle
   - starlette f985e49629: -0.00829, 2 files, no acyclicity/cycle change; likely driven by modularity/complexity/depth
   - datasette 9ca860e54f: -0.00670, 1 file, no cycle/acyclicity change
   - datasette b1289a73f9: -0.00560, 5 files, no cycle/acyclicity change

4. CYCLE REGRESSIONS WITH MODEST SCORE: 4 of the 5 cycles had score drops <0.005 (range -0.0015 to -0.0028), suggesting the score metric sometimes misses cycle-introducing commits or weighs them lightly.

5. SUGGESTED INVESTIGATION:
   - Are the 4 non-cycle score drops driven by complexity/modularity regressions? Extract cc_mean, raw_modularity, max_depth for these 4 vs typical commits
   - Do the 4 non-cycle score drops represent real structural risk (refactors, large complexity increases) that cycles miss?
   - Should the 0.005 threshold be reclassified as two distinct thresholds (one for cycles, one for other metrics)?

This is an honest gap: the document uses 0.005 to dismiss score as noise, but should investigate whether the 5 commits exceeding 0.005 form a structurally coherent category that the score is correctly identifying.

### [major] Post-hoc criterion selection: 98% threshold appears arbitrary, not principled  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md lines 106-109
- **claim**: The document selects 0.005 as the threshold to separate 'trivial' score drops from 'real' ones. This threshold appears chosen post-hoc to achieve a clean 98% / 2% split, lending false precision to a meaningless categorization.
- **evidence**: No score-based measurement error study is cited. No domain expert (e.g., architect, human reviewer) has validated that 0.005 is perceptible or meaningful. The threshold is simply asserted in the context of concluding 'score should be advisory.' This is motivated reasoning: define 'trivial' such that most events fall into it, then conclude most events are trivial. The alternative framing—'5 commits had substantial score regressions (0.5% rate, similar to cycle rate)'—is equally supported by data but not presented.
- **impact**: The document uses this threshold to justify excluding score from blocking decisions in the Q1b protocol (line 163: 'score regression beyond the 0.005 noise floor'). But Q1b is an experiment design for measuring agent behavior. If the threshold is arbitrary, the experiment may reject the null hypothesis for the wrong reasons or accept it when the signal is real but muted. The implications for product design are significant.
- **fix**: Either (1) conduct a separate psychometric study to determine what score delta is perceptible to human reviewers, or (2) use a data-driven method (e.g., clustering, change-point detection) to identify natural thresholds in the score-delta distribution, or (3) explicitly acknowledge the threshold is arbitrary and present results both with and without it.
- **notes**: The ticket must capture that: (1) The 0.005 threshold in INLOOP_PREVALENCE_EMPIRICS.md Finding 3 (lines 106-109) lacks any empirical validation—no psychometric study, no measurement-error study, no domain-expert review has demonstrated this delta is perceptible or meaningful. (2) The threshold is post-hoc: empirical data was collected, then the distribution was analyzed, and 0.005 was chosen because it cleanly separates 98% from 2%. This is data-driven but not principled. (3) The distribution shows no natural clustering around 0.005; the largest gap is between -0.008290 and lower values. (4) The Q1b experimental protocol (line 163) uses this threshold to define "structurally-bad diffs," which means the entire A/B test's outcome will depend on an unvalidated cutoff. (5) The document cites SCORING.md's own principle that "thresholds must be derived empirically from a benchmark population rather than asserted"—but archy's 0.005 threshold violates this: it's asserted post-hoc from a single study, not derived from a data-driven method like clustering or change-point detection. Recommendation: Conduct a separate study (psychometric or change-point detection) to identify natural thresholds before deploying the Q1b protocol, or explicitly acknowledge the threshold is arbitrary and present Q1b results both with and without it.

### [major] Q1b outcome definition includes post-hoc 0.005 threshold unvalidated in the corpus  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md line 163
- **claim**: The Q1b protocol defines 'structurally-bad diffs' to include 'score regression beyond the 0.005 noise floor.' But this threshold was not validated separately—it's introduced as a conclusion from Finding 3 ('this is the right shape'). Using it as an outcome criterion in Q1b will circularly reinforce the finding that led to its choice.
- **evidence**: Line 163: 'rate of structurally-bad produced diffs = introduced cycle OR declared-layer / contract violation OR score regression beyond the 0.005 noise floor established in Finding 3'. The 0.005 is described as 'established,' but it was asserted, not tested. If the Q1b experiment uses this outcome metric and finds no difference between arms, it could be because the threshold excludes real damage, not because archy is ineffective.
- **impact**: Q1b is the definitive follow-up experiment. If its outcome metric is biased by circular logic from Q1a, the experiment may fail to detect archy's value even if it exists. This is a design contamination risk.
- **fix**: Either (1) validate the 0.005 threshold independently in Q1b (e.g., run both arms, measure with and without the threshold, report sensitivity), or (2) use a more conservative threshold (e.g., any score drop) as the primary outcome and report 0.005-filtered results as secondary.
- **notes**: This is a design contamination risk that deserves explicit mitigation:

1. **Root cause:** The 0.005 threshold was identified descriptively from Q1a (observational, human data) but is being applied prescriptively in Q1b (experimental, agent data). No sensitivity analysis validates that this threshold generalizes to agent-produced code.

2. **Scope implications for Q1b:**
   - The primary outcome metric may systematically undercount real damage if agents produce score regressions at different magnitudes than humans
   - The "false negative" risk is that Q1b could fail to detect archy's value even if it exists, if archy prevents damage that manifests below the 0.005 threshold

3. **Recommended mitigations (as suggested in the finding):**
   - Option A (preferred for rigor): Run Q1b with dual metrics—report results with and without the 0.005 filter. Use the unfiltered score-drop rate as primary, filtered as secondary, to measure sensitivity.
   - Option B (minimum): Validate the 0.005 threshold independently in Q1b pilot data before committing to it as the outcome boundary.
   - Option C: Use "any score drop" as primary outcome (conservative, high power to detect if archy helps), and report the 0.005-filtered results as secondary (hypothesis about specificity).

4. **Decision rule impact:** The current protocol (line 168-169) stakes validation on "arm A's structurally-bad rate materially below arm B's." If the outcome metric systematically excludes a class of damage, the decision rule cannot detect it.

5. **Note:** Cycles and declared-layer violations remain FP-free and are unaffected by this concern; the issue is specific to the score-regression component of the outcome definition.

### [minor] Sampling bias toward recent history acknowledged but not quantified or corrected  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md lines 146-149
- **claim**: Document states: 'Silent skips bias `src/`-layout repos toward recent history... requests... yielded N=72 of 100 because pre-migration commits have no `src/requests`'. The 28% data loss is disclosed but treated as a minor limitation, not as a potential systematic bias.
- **evidence**: psf/requests was sampled at 100 commits, but only 72 were buildable (28% failure rate due to src/ layout migration in 2023). The document acknowledges this creates a recency bias but does not quantify its impact. It's possible (even likely) that the post-2023 codebase has different structural patterns than the pre-2023 codebase. If the missing 28% happened to be high-refactor periods with different cycle rates, the base rate could be off.
- **impact**: Requests contributes 1 of 5 cycle regressions (20% of the events). If that repo's sample is biased, the overall prevalence rate (0.5%) could be systematically over- or under-estimated. The impact is directional but magnitude unknown. This weakness is acknowledged ('honest limitations') but no corrective action is taken (e.g., sensitivity analysis, stratification, separate reporting).
- **fix**: Report cycle and score regression rates separately for 'full-history' repos and 'post-migration' repos (requests, click, flask). If rates differ significantly, re-calculate overall prevalence with and without the biased subset.
- **notes**: **Confirmed Finding**: The bias is real and treated as a "limitation" without quantification or correction. However, severity should remain "minor" because:

1. **The bias is isolated**: Only requests suffered sampling loss (28%); click and flask (also src/ layout) had 0% loss. The cause is specific to requests' migration timing, not a systematic flaw in the methodology.

2. **The impact is negligible on the headline result**: The 0.5% cycle regression rate changes by ~0.07pp if requests is excluded (0.47% → 0.40%). This is sub-noise-floor compared to the already-small absolute event count (5 events). The worst-case scenario (28 missing requests commits all having cycles) would only raise it to 3.08%, which is implausibly catastrophic and contradicts the repo's own behavior.

3. **No directional bias**: The requests cycle event (1.39% rate, 1/72) is actually HIGHER than non-requests repos (0.40%, 4/1000), so if anything, the sampling bias makes requests APPEAR worse than the true corpus average, not better.

4. **Suggested improvement** (not requirement): The document should add one line: "Excluding requests entirely yields 0.40% (4/1000), confirming that the migration bias does not materially affect the reported prevalence rate." This would convert an unstated assumption into a stated validation without changing any conclusions.

**Scope**: The ticket should clarify that while the finding is technically accurate (bias exists, not quantified), the practical impact on the main finding is negligible, and no major revision is needed. The document is honest about the limitation, just not quantitatively precise about its magnitude.

### [minor] Unvalidated assumption: 'multi-module tangles' (new-SCC size) are categorically 'non-trivial'  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md lines 82-84 and 97-99
- **claim**: Document claims the 5 cycle events represent 'non-trivial' structural damage because 4 of 5 have new-SCC size >2. But 'trivial' vs 'non-trivial' is not defined. A 2-cycle may be refactoring-safe or long-lived; a 3-cycle may be impossible to untangle. The assessment appears intuitive rather than empirical.
- **evidence**: Document: 'only 1 of 5 was an easy 2-module cycle' (line 84). Implication: size 2 is 'easy' and therefore trivial. But nowhere in the document is this judgment validated. No citation to software-engineering literature on cycle severity. No measurement of human effort to resolve the 5 cycles. No follow-up in the repos to see if cycles persisted or were fixed.
- **impact**: If some of the 5 cycles were actually trivial (easy refactoring artifacts, later resolved), the prevalence of 'meaningful' cycles could be even lower than 0.5%. Conversely, if the 2-cycle is architecturally critical, classifying it as 'trivial' understates the severity. The document's framing suggests all 5 are concerning, but this is not empirically grounded.
- **fix**: For each of the 5 cycle-introducing commits, check the subsequent git history: was the cycle resolved? How long did it persist? If high-leverage domain experts (the repository maintainers) resolved it quickly, it was likely refactoring-safe. If it persisted for months, it was likely consequential. Add this temporal follow-up to the data.
- **notes**: This is a methodological clarity issue, not a factual error. The core empirical finding (0.5% prevalence, 7x file concentration, 5 events with SCC sizes 2-8) is correct. However, the interpretation ("these 5 are non-trivial, so they matter") rests on an assumption (that SCC size correlates with refactoring difficulty) that is never validated. The document would be stronger if it: (1) acknowledged that "trivial" vs "non-trivial" are working assumptions, not measurements; (2) added a follow-up check: for the mkdocs 2-cycle (commit c5d3555594), was it resolved? How long did it persist?; (3) cited or argued why SCC size should predict refactoring difficulty; (4) added these missing empirical checks to the "Honest limitations" section. The finding is valid but the proposed action (temporal follow-up per git history) would genuinely strengthen the paper and is feasible given the 5 known commits.

### [minor] Double standard: score regressions dismissed as 'noise' but cycles treated as real at same base rate (0.5%)  (confirmed)
- **loc**: docs/research/INLOOP_PREVALENCE_EMPIRICS.md §2 and §3
- **claim**: Document argues cycles (0.5% rate) are important and warrant blocking checks, while score regressions (29% rate, but 98% < 0.005 magnitude, leaving ~0.5% 'real' drops) should be only advisory. The effective rates are similar (~0.5%), but the framing is opposite: one is 'FP-free gate,' the other is 'noise.'
- **evidence**: Cycles: 5/1072 = 0.47%. Score drops >0.005: 5/1072 = 0.47%. The rates are identical. Yet Finding 2 frames cycles as actionable ('FP-free') and Finding 3 frames score as noise ('mostly trivial'). This suggests the distinction is not empirical (rate, impact) but predetermined (cycles good, score bad).
- **impact**: The document's logic justifies design choices in Q1b (line 162-164): 'rate of structurally-bad produced diffs = introduced cycle OR declared-layer / contract violation OR score regression beyond the 0.005 noise floor.' If score drops and cycles are equally rare, why are they treated so differently in the outcome metric? This creates a potential double-counting or selection bias in the experiment design.
- **fix**: Either (1) apply the same 'real vs noise' filter to cycles (find a threshold for cycle 'triviality'), or (2) present cycles and score drops as commensurable signals and let the experiment design weight them equally, or (3) explicitly justify why cycles are categorically more trustworthy than score (e.g., FP rate analysis, maintenance expert consensus).
- **notes**: The asymmetry is real in framing/narrative weight but defensible if completed. The ticket should require: (1) empirical analysis of the 5 large score drops to match the severity narrative given for cycles (commit size, SCC complexity if applicable, what they reveal about code quality), OR (2) explicit justification in the document for why cycle signals are theoretically/empirically more trustworthy than equivalent-prevalence score signals (e.g., FP rate analysis, expert consensus, or a principled reason to weight them differently in Q1b outcomes). The current state leaves a gap: identical rates but opposite design implications. Also note that 4 of 5 large score drops do NOT coincide with cycle regressions, suggesting they may be capturing distinct failure modes that deserve equivalent treatment in the experiment outcome metric.

## research / RESEARCH_METRICS+synthesis


### [major] Exemplar surfacing claim sourced to HN threads rather than peer-reviewed literature  (confirmed)
- **loc**: RESEARCH_METRICS.md §14c.4, lines 877-884; reiterated §14c.5
- **claim**: The claim that 'exemplar-based constraints proved phenomenally powerful' with '~75-80% style-match when the agent could see how a pattern was already implemented' is presented as coming from HN discussion threads (constraint-decay-hn, line 813).
- **evidence**: The ~75-80% figure and 'exemplar-based constraints proved phenomenally powerful' quote are attributed to 'the thread's most-repeated practical claim' and 'a separate report' (line 879-880), which are anonymous HN comments, not a citable paper or peer-reviewed study. The document cites 'constraint-decay-hn' as the source, which links to a news.ycombinator.com thread (ID 48256912). No DOI, arxiv, or academic publication supports this figure. The document later treats this as motivation for roadmap work (#138), grounding design decisions on unsourced internet comments.
- **impact**: Features and design decisions (#138) are being motivated by Internet forum commentary that lacks peer review, reproducibility, or empirical validation. This inverts the document's stated commitment to rigor. HN threads are useful for public reaction but should not be cited as evidence for empirical claims without independent verification.
- **fix**: Either (1) remove the ~75-80% figure and quote as an unsourced claim, replacing it with 'HN discussion suggests exemplar-based approaches have perceived value,' or (2) find and cite an actual peer-reviewed source if one exists. The design discussion can remain, but with explicit framing that it is 'motivated by practitioner intuition, not empirical evidence.'
- **notes**: 1. The ~75-80% figure and the quote "exemplar-based constraints proved phenomenally powerful" ARE sourced from the HN thread (news.ycombinator.com/item?id=48256912), not from peer-reviewed literature or the Constraint Decay arxiv paper.

2. The phrase "a separate report" is misleading - it's uncited and no separate reference exists for it.

3. The document IS transparent that the claim comes from "the thread," but the presentation in a section analyzing the Constraint Decay paper (which IS peer-reviewed) creates potential confusion about which claims are empirically grounded.

4. The FUTURE.md file (which cross-references this same claim) explicitly states "the Constraint Decay HN thread's most-repeated practical finding is that..." - so the document does know this is from HN discussion.

5. Roadmap item #138 (Positive exemplar surfacing) is motivated directly by this HN-sourced claim. The document does note (FUTURE.md and RESEARCH_METRICS.md §14c.5) that this feature "needs a bench validation" - acknowledging it's currently unvalidated.

RECOMMENDED FIX: Change line 878-880 from:
"The thread's most-repeated practical claim is that showing the agent a good example beats describing the rule ("exemplar-based constraints proved phenomenally powerful"; a separate report of ~75-80% style-match when the agent could see how a pattern was already implemented)."

To something like:
"The thread's most-repeated practical claim (though unsupported by empirical evidence) is that showing the agent a good example beats describing the rule. One commenter reported anecdotally observing ~75-80% style-match improvement when the agent could see how a pattern was already implemented, and another claimed 'exemplar-based constraints proved phenomenally powerful' — both observations lack peer review or reproducibility."

OR, if the claim is deemed important enough: Find and cite the actual source of the ~75-80% figure, or remove the specific percentage and describe it as "anecdotal improvements" instead of "~75-80%".

### [major] Dead source links acknowledged but claims still used as support  (confirmed)
- **loc**: AGENT_CAUSAL_REASONING_SYNTHESIS.md §10 Sourcing gap, lines 376-384
- **claim**: The document states: 'Two of the source article's three "Related Work" links... are **dead on the author's own site (HTTP 404 as of 2026-05-27)**... Only their one-line Related Work descriptions are available, used above without their full text.'
- **evidence**: The document cites this explicitly: two referenced works ('AI Engineering as Team-Based Work' and 'engineers-need-to-know.html') are dead links, but the document admits it used their one-line descriptions from the source article's Related Work section. These one-line descriptions are presented as sufficient to use the ideas as support in §6-§7, yet they are literally unavailable for verification. The document also uses a claim from these dead links to support design implications: 'the real gains from AI come from improving the shared work between engineers' (line 854).
- **impact**: A synthesis document that positions itself as evidence-based is using claims from sources that no longer exist and cannot be verified. The document is honest about the dead links but uses them anyway. This violates the principle that citations should be verifiable. It also sets a precedent: if major source claims are dead and the synthesis still uses them, readers cannot audit the reasoning.
- **fix**: Remove all claims sourced to the dead links from §6-§7 and the Related Work section. If the claims are important, find a live, verifiable source. Alternatively, frame these as 'according to the Phroneses article's summary of [dead link]' and mark them as unverifiable rather than presenting them as evidence.
- **notes**: CONFIRMED ISSUES:
1. The dead links (ai-engineering-team-based-ai.html, engineers-need-to-know.html) are correctly identified as HTTP 404 as of the document's date (2026-05-27).

2. The one-line description "the real gains from AI come from improving the shared work between engineers" is used (paraphrased) at line 252-253 in §7 as supporting evidence for design recommendations.

3. No other text from these sources is available to verify the full context of the claim.

MITIGATING FACTORS:
- The document explicitly acknowledges the dead links in §10 (Sourcing gap), labeled as an "honest note"
- The document is transparent about using one-line descriptions
- The primary source (Phroneses article) is live and verifiable
- The claim is presented as coming from that article's Related Work section, not as a direct primary source

SCOPE FOR TICKET:
- Clarify whether the main Phroneses article actually makes the "shared work" claim in its body, or if it only appears in the Related Work entries
- If the claim is unique to the dead links' descriptions, consider: (a) finding a live source that supports the same claim, (b) removing the dead-link-derived claims from §7's supporting evidence, or (c) explicitly reframing §7 to note that the "shared work" claim is based on unverifiable Related Work descriptions
- The claim is central to §7's logic (establishing that the bottleneck is in review/coordination, not individual coding), so if it must be removed, §7 would need to rely entirely on the 2025-2026 review literature citations (which are live)

### [major] METR 19% slowness claim uses correct source but may extend interpretation beyond evidence  (confirmed)
- **loc**: AUTONOMY_CONTINUUM_SYNTHESIS.md, lines 36-37; AGENT_CAUSAL_REASONING_SYNTHESIS.md lines 83-85
- **claim**: 'a controlled study found AI tools made experienced developers ~19% *slower* ([*Agentic Coding in Production*][tianpan])' is cited as evidence that 'visible-metric optimization diverges from real value'
- **evidence**: The citation points to both a blog post (tianpan) and arxiv reference [metr-study]: https://arxiv.org/abs/2507.09089 (the actual METR RCT). However, the document frames this as general evidence that 'visible-metric optimization diverges from real value' (line 36). The METR study measures developer productivity with AI tools, which is a different question than whether Goodhart's law applies to archy's score. The document uses this to argue archy's score could be gamed, but the METR study doesn't measure architecture metrics specifically - it measures overall development speed. This is motivated reasoning: finding a relevant-sounding study and citing it for a claim it doesn't directly support.
- **impact**: The METR finding is real and important, but it is being extended beyond what it measures. The study is about AI-assisted development speed, not about the gamability of architecture metrics. Using it to argue 'visible metrics get gamed' is plausible but not directly supported by the METR data. The connection is assumed, not proven.
- **fix**: Keep the METR citation for what it actually measures (productivity slowdowns with AI tools), but separate it from the Goodhart's law discussion. Either find a study that specifically examines metric gaming in architecture scoring, or acknowledge the Goodhart argument is theoretical, not empirically grounded in the METR data.
- **notes**: The finding is confirmed. The METR study citation should be revised or separated from the Goodhart's law argument. Specifically: (1) METR demonstrates that AI tool productivity benefits diverge from developer forecasts—this is about prediction accuracy, not metric gaming or Goodhart's law; (2) GitClear demonstrates actual metric gaming (quality declined while visible metrics rose), which is direct evidence of Goodhart's law; (3) these should not be grouped as supporting the same claim without distinguishing the phenomena. For the archy-specific risk (that archy's score could be gamed), the document itself acknowledges ("stated honestly") that the actual protections are the composite structure and advisory framing, not the METR evidence. The suggestion to separate METR from the Goodhart argument is sound. Either: find a study specifically examining architecture metric gaming, or reframe the METR citation to support only what it measures (AI tool productivity expectations vs. reality), and rely on GitClear or similar evidence for the metric-gaming part of the argument. This affects the credibility of the "score invites productivity theater" section but not the actual design conclusions reached (which are grounded in the composite-score and advisory-framing defenses).

### [major] Constraint Decay paper -9.1 pp penalty misframed as evidence of archy's value  (confirmed)
- **loc**: RESEARCH_METRICS.md §14c.4, lines 784-786
- **claim**: 'the quantified -9.1 pp is the cleanest external evidence yet that the architectural feedback loop has measurable value at generation time'
- **evidence**: The Constraint Decay paper measures the performance drop when architectural constraints are added (Clean Architecture layering costs -9.1 pp). The document frames this as evidence that archy's feedback loop would help. However, the paper did NOT test archy or any feedback loop—it tested static constraints given to the agent. The -9.1 pp is the *cost of the constraint*, not the *benefit of feedback*. The document extrapolates this to argue for archy's value, but the causal chain is: constraint is hard for agent → archy feedback helps agent satisfy constraint → agent produces fewer regressions. The paper only measures the first step (constraint is hard). The document is inferring the third step (feedback helps) from the second step (agent struggles) without direct evidence.
- **impact**: The document cites Constraint Decay as 'the cleanest external evidence' for archy's value, but it is actually evidence that agents struggle with architectural constraints—which is *motivation* for archy, not *evidence that archy works*. The INLOOP_PREVALENCE_EMPIRICS study actually tests whether archy catches regressions (it does, in 0.5% of commits), but that study is presented separately, not as direct validation of the Constraint Decay claim. The logical leap is significant.
- **fix**: Separate 'evidence that agents struggle with architecture (Constraint Decay)' from 'evidence that archy helps (INLOOP_PREVALENCE_EMPIRICS).' The current framing conflates motivation with validation. Reframe Constraint Decay as motivation: 'Constraint Decay provides motivation for archy: agents struggle with architectural constraints. Whether archy's feedback loop improves outcomes is the Q1b question in INLOOP_PREVALENCE_EMPIRICS.'
- **notes**: The ticket should clarify that the -9.1 pp is evidence of the *motivation* for archy (agents struggle with architectural constraints), not *validation* of archy's feedback loop. Lines 785-786 should be reframed to explicitly state: "the -9.1 pp provides motivation for archy's feedback loop: agents struggle with static architectural constraints in prose. Whether archy's MCP-based dynamic feedback actually improves outcomes is the subject of Q1b in INLOOP_PREVALENCE_EMPIRICS.md, which remains to be tested." The distinction between "evidence agents need help" vs. "evidence archy provides that help" is load-bearing for honest framing of the research contribution.

### [minor] MacCormack core/periphery '75-80% finding' lacks citation and appears speculative  (uncertain)
- **loc**: RESEARCH_METRICS.md §3, lines 218-223
- **claim**: MacCormack's work is cited as showing 'Empirical finding across 75-80% of real systems: there is a single dominant core, and smaller cores correlate with healthier maintainability.'
- **evidence**: The reference is [maccormack-core-periphery] which resolves to HBS 10-059, an HBS research paper. However, the exact claim about '75-80% of real systems' having a single dominant core with correlation to healthier maintainability is not quoted or page-cited. The reference links to an abstract/cover page, not the full paper. The document cites this as empirical finding but provides no exact quote, no Table number, no p-value, and no sample size from the original work. This appears to be a paraphrase of a general pattern from the literature rather than a specific quantified finding.
- **impact**: A design decision (to report core size as a diagnostic alongside NCCD) is justified by a figure ('75-80%') that cannot be verified from the provided citation. If the original paper says something different (e.g., '70% of systems' or 'in the systems studied, 80%'), the claim becomes unsupported. The vagueness suggests the author is working from memory or secondary sources rather than the primary paper.
- **fix**: Either (1) read and quote the exact passage from HBS 10-059 that supports this claim with page numbers and confidence intervals, or (2) soften the language to 'MacCormack's work suggests...' and acknowledge the 75-80% figure is a rough summary, not a precise empirical result.
- **notes**: The ticket should capture: (1) The 75-80% figure is cited without page numbers, table references, sample sizes, or confidence intervals; (2) Core/periphery size is listed in the RESEARCH_METRICS summary table with an empty validation column, indicating zero empirical testing within the archy project; (3) The metric has never been implemented in code (no `core_size` computation exists in src/archy); (4) The design note (line 223) says to "report as a diagnostic, not a score axis" but this was never built; (5) Recommend: either (a) provide the specific page/table from HBS 10-059 that contains the 75-80% figure with sample size and confidence interval, or (b) reframe as "literature suggests..." or acknowledge the figure is a rough summary from secondary sources rather than a precisely validated empirical finding. The minor severity is appropriate because the metric is listed as deferred/recommended but not shipped, so the citation precision is lower stakes than if this drove a critical design decision that was actually deployed.

### [minor] Type-hint survey statistics (73%/41%) sourced to a Substack, not peer-reviewed research  (confirmed)
- **loc**: RESEARCH_METRICS.md §13, lines 606-608
- **claim**: 'A 2025 community survey found 73% of Python projects use type hints, but only 41% run a checker in CI ([source][type-hints-survey]).'
- **evidence**: The source is [type-hints-survey]: https://anujyadav.substack.com/p/type-hinting-and-type-checking-with - a Substack blog post by Anuj Yadav, not a peer-reviewed study, conference paper, or published survey. Substack posts lack peer review, reproducible methodology disclosure, sample-size verification, or confidence intervals. The document uses these figures (73%, 41%) to make a structural point about Python type coverage, but the methodological rigor is much lower than claimed.
- **impact**: A statistical claim (73% use types, 41% run checkers) is cited as 'a 2025 community survey' when it's actually a blog post. The phrasing suggests academic rigor when none exists. Readers treating this as evidence for a broader point are relying on unverified data. This matters because the document later uses type-hint statistics to motivate a decision NOT to ship type coverage as an archy metric (§13, rejected 2026-05).
- **fix**: Reframe as 'A 2025 Substack post by Anuj Yadav reported 73% of projects... (anecdotal)', or find an actual peer-reviewed survey (e.g., from a conference proceedings or journal) that measures the same statistics. If none exists, acknowledge this is based on blog analysis, not rigorous survey methodology.
- **notes**: The finding is factually accurate but the severity should be downgraded from "major" to "minor" for these reasons: (1) The document explicitly marks this section as "pre-study survey rationale, not a live recommendation" (lines 627-636), so the misleading framing is not actively driving current decisions. (2) The statistics appear in a discussion section that was eventually rejected anyway—type-hint coverage was not adopted as a metric. (3) The document's own subsequent empirical study in TYPE_HINT_COVERAGE_EMPIRICS.md examined these claims before rejecting them. The reframing suggested (changing "2025 community survey" to "A 2025 Substack post by Anuj Yadav reported") is still reasonable for accuracy, but this is a documentation clarity issue rather than a decision made on unreliable data, since the decision was later reversed based on empirical study. The ticket should note that the phrasing should be updated for honesty, but the impact on archy's actual design is null.

### [minor] Vulture false-positive study uses only 15 spot-checks per project (statistically underpowered)  (confirmed)
- **loc**: RESEARCH_METRICS.md §12, lines 518-549
- **claim**: 'Spot-checking findings on FastAPI, pytest, and Django (15 random findings each)' resulted in 15/15 false positives in each project, leading to the conclusion that Vulture has prohibitive false-positive rates for Python and should not be shipped.
- **evidence**: The study checked 15 random findings per project (45 total across 3 projects) and found 100% false positives, leading to the strong conclusion 'A naive vulture-style scan in archy would generate so many false positives that ignoring them would become the default workflow' (line 571-573). However, 15 findings is a convenience sample, not a powered statistical sample. The study provides no confidence intervals, no power analysis, and no justification for why 15 is sufficient. The document does not disclose how the 15 findings were selected (were they random? sequential? stratified by confidence level?).
- **impact**: A design decision (do not ship dead-code detection) is justified by a small, non-powered sample. While the findings may be correct (all 15 false positives suggests a real problem), the rigor is overstated. A defender of Vulture could argue that with 1,827 default-confidence findings (Django), 15 spot-checks is a 0.8% sample and insufficient to conclude 100% FP rate. The study conflates 'all 15 we checked were false positives' with 'the tool is fundamentally broken for Python,' which is a leap.
- **fix**: Either (1) increase the sample to 50-100 random findings across all projects to achieve statistical power, or (2) reframe the conclusion to 'our 15-finding sample suggests high FP rates; a larger validation is needed before shipping.' This is closer to what the data actually shows.
- **notes**: The ticket must record: (1) The 15-finding sample per project is statistically underpowered and lacks formal power analysis. The document should either increase the sample to 50-100 findings for statistical rigor OR add explicit language like "our 15-finding exploratory sample suggests high FP rates; a larger validation is needed before shipping dead-code detection." (2) The sampling method should be documented explicitly (was it stratified by confidence level? sequential from the Vulture output? truly random?). (3) Despite the statistical gap, the seven false-positive patterns identified (pytest fixtures, route handlers, Django settings variables, pluggy entry points, Pydantic validators, Protocols/ABCs, vendored APIs) appear to be genuine and reproducible, not sampling artifacts—these should be preserved in any revision. (4) The design decision "do not ship dead-code detection" is justified by the qualitative evidence more than the quantitative claim; the ticket should clarify that the barrier to shipping is not the sample size alone but the *mechanism* (Python's dynamic dispatch patterns are too widespread for Vulture to handle without prohibitive false positives). The revision should reframe as: "We identified seven endemic Python patterns that cause Vulture false positives; a 15-finding exploratory sample suggests 100% FP rate in these categories, but a larger validation (50-100 findings, ideally stratified across projects and confidence levels) is required before a shipping decision."

### [minor] Correlation r=0.000 for NCCD vs depth claimed as 'orthogonal' without explaining the zero  (uncertain)
- **loc**: RESEARCH_METRICS.md §3, lines 176-197
- **claim**: The document claims NCCD and max_depth are 'empirically orthogonal' because Pearson(NCCD, max_depth) = 0.000, and concludes they 'capture different things': max_depth is worst-case, NCCD is average-case.
- **evidence**: The correlation is indeed 0.000 (line 179), supporting orthogonality. However, the causal story ('max_depth is worst case, NCCD is average case') is a post-hoc narrative fit to a result. The document does not explain WHY they should be uncorrelated if they both measure some aspect of coupling/reach. A graph can be shallow and wide (high NCCD, low depth) or deep and narrow (low NCCD, high depth)—this is *inherently* suggestive of some structure. The r=0.000 is surprising and deserves interrogation: is it a data artifact (small N=9 libs?), or does it reveal something about the sample's structure? The document treats the lack of correlation as validation without exploring why it's there.
- **impact**: The narrative fit ('they capture different things') becomes the interpretation rather than investigation. For a project claiming 'rigor,' unexplained zero correlation in a small sample (N=9) should trigger caution, not celebration. This is confirmation bias: the result validates the hypothesis, so no deeper interrogation follows.
- **fix**: Add a paragraph investigating the r=0.000 result: 'We expected some negative correlation because a system cannot be both maximally shallow and minimally coupled. The absence of correlation suggests [hypothesis about the sample structure]. This deserves further investigation on a larger sample.' This is honest science.
- **notes**: The ticket should capture that the RESEARCH_METRICS.md §3 (lines 176-197) makes a mathematically sound claim but lacks rigor in interpretation. Recommended additions: (1) Acknowledge that max_depth and average reach are structurally independent properties, so r≈0 is expected, not surprising. (2) Clarify sample size (N=10, not 9) and note that validation on larger codebase sample would strengthen confidence. (3) Replace "refuting an earlier draft's worry" with a more neutral framing that acknowledges this confirms expected independence rather than discovers it. The narrative examples are factually correct and helpful, so keep them. The severity is MINOR because the documentation is technically sound in its recommendations (NCCD and max_depth should both ship as complementary signals), just incomplete in its explanation. No change to shipped code is needed.

## research / SCORE_SHAPE_REDESIGN


### [major] Depth-overall correlation claim conflates distinct findings without clear attribution  (confirmed)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:51-57
- **claim**: The document claims depth is the least correlated axis with overall and concludes the OECD breach is cosmetic. However, this conflates axis-pair coupling (the original problem) with individual axis leverage (a separate concern).
- **evidence**: The finding at lines 51-57 is presented as overturning the original framing: 'A fully honest finding ran sideways to the original framing.' But the cited correlation (|r(overall, depth)| <= 0.187) is about depth's individual weight, not about whether acyclicity and depth are coupled (r = -0.641). The document then concludes coupling is cosmetic because depth is down-weighted. This reasoning is invalid: two axes can be correlated while both having weak individual leverage. The coupling still constrains the design space regardless of depth's weight in the aggregator.
- **impact**: Readers may misinterpret this as showing the original axis-coupling problem is minor. The weak individual leverage does not eliminate the coupling; it only reduces its operational impact on the score. The conclusion to not redesign axes is justified, but not by the cosmetic logic presented here.
- **fix**: Clearly separate the two independent findings: (1) Axis-pair coupling exists (acyclicity-depth r=-0.641); (2) Depth has weak individual leverage on overall. State that weak leverage does not eliminate the coupling problem; it only reduces practical impact. Then argue: 'All redesigns fixing the coupling break rank stability or actionability. Given the weak leverage, tolerating the uncoupled correlation is more defensible.'
- **notes**: The finding is valid: there IS a logical conflation at lines 51-57. However, scope should clarify: (1) The flawed logic doesn't undermine the decision, which is based on stability/actionability analysis in lines 28-40. (2) The author's proposed LEARNINGS.md update (lines 291-296) correctly distinguishes the two concepts, suggesting awareness of the distinction even though the 51-57 passage itself conflates them. (3) The core issue is presentation clarity, not an incorrect conclusion. The ticket should focus on fixing lines 51-57 to align with the clearer reasoning in the proposed LEARNINGS.md update: make explicit that weak individual leverage does not eliminate the coupling problem, only its operational impact on scoring. The suggested action in the finding (separate findings 1 and 2, then argue why tolerating the coupling is defensible given weak leverage) matches the quality of reasoning in the proposed LEARNINGS.md language.

### [major] Actionability argument confuses OECD standard with tool surface limitation  (confirmed)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:117-130
- **claim**: The actionability rejection conflates whether the axis formula is conceptually actionable (OECD sense) with whether archy diagnostic tools can decompose the regression into component causes (tool surface limitation).
- **evidence**: The document cites OECD Section 2 on actionability while discussing diagnostic message ambiguity. But OECD actionability is about indicator clarity to end users, not about decomposability of tool output. If archy were modified to surface both max_depth and largest_scc separately, the axis would be fully OECD-actionable. The real constraint is tool surface, not OECD standards.
- **impact**: Obscures the real constraint. Conflating tool limitations with OECD standards makes the decision appear more principled and hides the opportunity to revisit if diagnostics improve.
- **fix**: Separate concerns. State: The axis is conceptually actionable per OECD standards. However, archy's score --breakdown would need enhancement to surface max_depth and largest_scc separately, deferring this redesign until that tool evolution is scheduled.
- **notes**: The core decision (don't replace axes) may still be correct, but for different reasons than stated. The document should:

1. Distinguish between "OECD actionability" (refactoring actions exist) and "diagnostic clarity" (tool can decompose regression causes)

2. Acknowledge that depth_with_scc_penalty is OECD-actionable (breaking chains and cycles are both independent good practices)

3. Reframe the constraint as a tool surface limitation: "archy score --breakdown would need to surface max_depth and largest_scc separately"

4. This opens a path forward: "if tool diagnostics improve in a future version, revisit whether depth_with_scc_penalty becomes viable"

This matters because it:
- Separates concerns (design principle vs implementation constraint)
- Preserves the option to revisit if tooling improves
- Makes the OECD argument more rigorous (doesn't conflate different gates)
- Aligns with the document's pattern elsewhere (e.g., rank stability, which IS an independent gate that legitimately kills these combinations)

### [minor] OECD actionability gate applied selectively, with weaker rigor on aggregator changes  (uncertain)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:114-130 (actionability rejection) vs. lines 269-271 (aggregator acceptance)
- **claim**: The actionability gate is invoked to reject depth_with_scc_penalty due to ambiguity in diagnostic output, yet the same gate is not applied to aggregator changes that fundamentally shift what the score measures.
- **evidence**: For depth_with_scc_penalty, the document rejects it because diagnostic output becomes ambiguous (chain growth vs. SCC growth). Yet for aggregators, the correlation profile shifts significantly: r(o,a) drops from +0.552 to +0.481 (PGM) or +0.450 (MPI), and r(o,c) rises to +0.612 (MPI). An agent diagnosing why overall changed must recalibrate its understanding of axis weights. The actionability standard is violated the same way, just the diagnostic surface differs.
- **impact**: Creates an inconsistent decision framework. Depth changes are rejected for creating diagnostic ambiguity; aggregator changes that create greater ambiguity (shifting leverage across all axes) are recommended as optional.
- **fix**: Apply the actionability gate uniformly by requiring that any score-shape change (axis or aggregator) preserve diagnostic interpretability with equal rigor. Or explicitly stratify gates by consequence (e.g., rank-stability gates apply to all changes; actionability gates apply only to axis changes). Document which standard is used for each recommendation.
- **notes**: INCONSISTENCY IS REAL BUT NOT ACTIONABILITY VIOLATION: The document applies different OECD gates to axes vs aggregators (lines 268-271) without explicitly justifying this distinction. This creates ambiguity for readers about whether the gates are applied inconsistently or just to different concerns.

NUMBERS VERIFIED: Finding's correlations are correct (geomean r(o,a)=0.552 vs PGM 0.481 vs MPI 0.450; r(o,c) shifts to 0.612 under MPI).

DOCUMENTATION GAP: The document should clarify:
1. Why actionability (clear refactoring action) is required for axes but not aggregators. Currently inferred but not stated: axes are meant as standalone diagnostic signals; aggregators are meant as mathematical combinations.
2. Whether leverage shifts are formally gated. Currently, the only aggregator gate on this dimension is "sensitivity profile no flatter than existing geomean by more than 50%" (line 270), which is about profile uniformity, not about the magnitude of shifts.
3. That axes remain unambiguous under aggregator changes (unlike depth_with_scc_penalty where the axis itself becomes ambiguous).

SEVERITY DOWNGRADE: From "major" to "minor" because the underlying technical approach (different gates for axes vs aggregators) is defensible; the issue is primarily about documentation clarity and explicit design rationale, not a substantive methodological flaw.

### [minor] OECD cosmetic argument inverts the threshold logic without justification  (confirmed)
- **loc**: docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md:51-57, 14-20
- **claim**: The document calls the OECD redundancy breach cosmetic because depth correlates weakly with overall. But the OECD gate concerns axis-pair independence, not individual axis leverage. Calling a correlation-matrix problem cosmetic due to weak aggregator leverage is a non sequitur.
- **evidence**: The OECD redundancy threshold cited in line 14 is |r| < 0.7 between axes. The two problematic pairs at -0.641 and -0.581 meet OECD criteria for concern. But the document then argues these pairs create smaller operational gaming surface because depth has low individual leverage. The OECD handbook gate is about whether axes can be independently varied, not about individual axis weight. The coupling is a real design constraint per OECD standards, regardless of depth's aggregator weight.
- **impact**: The conclusion justifies avoiding redesign but mixes concerns: axis-pair redundancy (OECD-defined problem) is conflated with individual axis impact (engineering concern). The cosmetic framing obscures the actual trade-off: redesigns fix coupling but break rank stability or actionability.
- **fix**: Remove cosmetic language. Restate as: 'The axis-pair coupling is real and meets OECD redundancy threshold for concern. However, all redesigns that eliminate it require breaking either rank stability or actionability. The weak individual leverage of depth is a secondary finding that reduces practical impact but does not eliminate the design constraint.'
- **notes**: The issue is real but narrower than claimed. The document should clarify that weak individual axis leverage reduces operational gaming exposure but does not address the OECD design-space constraint. The distinction matters because future designs might optimize for different goals (e.g., architectural explainability) where axis independence becomes load-bearing again. Suggested fix: Remove or reframe lines 51-57 to separate two findings: (1) the axis-pair coupling meets OECD threshold for concern, (2) the depth axis has weak individual leverage on the score under all aggregators. Then state: "These are independent findings. The coupling is a real design constraint per OECD standards. The weak leverage explains why the practical gaming exposure from the coupling is limited, but not why the coupling itself should be ignored as a design-space property." The decision to not redesign is already well-justified by actionability/rank-stability gates (lines 28-40), so the secondary finding needs only clarification, not removal.

## research / SCORING-methodology


### [major] Depth axis barely moves overall score despite two moderate correlations  (confirmed)
- **loc**: docs/SCORING.md lines 465-487; docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md line 53
- **claim**: The document asserts axis independence is validated empirically and that depth is orthogonal to other axes. However, the empirics reveal depth correlates only weakly with overall (`|r| ≤ 0.187` under every tested aggregator), making depth optimization 'essentially toothless against the score' regardless of whether the moderate pairwise correlations are an architectural problem.
- **evidence**: SCORE_SHAPE_REDESIGN_EMPIRICS explicitly states (line 53-57): 'under *every* tested aggregator, `depth` is the axis least correlated with `overall` (`|r(overall, depth)| <= 0.187`... The two moderate pairs therefore create a smaller *operational* gaming surface than the design language suggests; the OECD breach is real but cosmetic in practice.' Yet SCORING.md lines 39-41 claims 'A weak score on any axis should pull the overall down sharply. Improving the overall should require improving every axis.' This is literally contradicted by the empirics for depth.
- **impact**: Users reading the design-goals section believe all five axes have meaningful leverage on overall score, and can be misled into prioritizing depth improvements that empirically have negligible effect. The non-compensatory property the document claims 'makes the score hard to game' is operationally true only for 4 of 5 axes (modularity, acyclicity, equality, complexity).
- **fix**: Update 'Design goals' section 2 to note that the actual weak-axis property holds for four of five axes. Alternatively, update it to state 'A weak score on modularity, acyclicity, equality, or complexity should pull the overall down sharply. Depth optimization is uncorrelated with overall regardless of other axes' values, making depth improvements optional rather than required.'
- **notes**: 1. The maximum observed |r(overall, depth)| is -0.187 under harmonic mean; under current geometric mean it's -0.135. Both support the ≤ 0.187 bound claim. 2. The finding correctly notes that modularity, acyclicity, equality, and complexity all have non-trivial leverage (|r| > 0.450), so the non-compensatory property does bite on four of five axes as designed. 3. Recommended fix: Update Design Goals section 2 to explicitly note the depth exception. Either: (a) change "any axis" to "modularity, acyclicity, equality, or complexity" and note depth separately, OR (b) add a parenthetical caveat that depth correlates only weakly with overall. 4. The issue is not methodological—the research and analysis are sound—but documentation clarity: a reader of Design Goals alone would be misled about depth's actual leverage on the score.

### [major] Magic number /8 divisor justified by single proprietary project data point, not public benchmark  (confirmed)
- **loc**: docs/SCORING.md lines 285-290; src/archy/score.py line 13; commit a15f5d9
- **claim**: The document states the /8 divisor 'widened from /5 (v0.20) to /8 (v0.23) after the original calibration drove the geomean to 0.000 on realistic backends whose `cc_mean` lands in `[6, 9)`.' The justification relies on 'governingdocs/backend' as a validator/parser-heavy motivating case with cc_mean=6.48.
- **evidence**: The commit message for a15f5d9 explicitly cites 'governingdocs/backend (the motivating real-world case): overall 0.000 -> 0.495, complexity 0.000 -> 0.297' as the primary justification. However, governingdocs/backend is NOT in the published benchmark (bench/projects.yaml contains 28 projects: 27 public open-source projects + archy itself). The divisor change is justified by a private codebase not visible to users or auditors. The SCORE_SHAPE_REDESIGN_EMPIRICS document (line 250) mentions governingdocs as 'a 28th data point' used in post-hoc correlation analysis, but it was never in the baseline bench when the divisor decision was made.
- **impact**: The core calibration number (/8 instead of /5) that directly affects all complexity scores is based on proprietary project data. For users without access to governingdocs/backend, the divisor choice appears to be a magic number. If governingdocs/backend is unrepresentative (e.g., exceptionally parser/validator heavy even among parser-heavy codebases), the divisor is miscalibrated for public projects.
- **fix**: Either: (1) add a placeholder or synthetic validator/parser-heavy test case to bench/projects.yaml so the calibration decision is publicly reproducible, or (2) document explicitly that the /8 divisor was calibrated against a private 28th data point and is thus not fully reproducible from the published benchmark.
- **notes**: The /8 divisor affecting all projects' complexity scores is indeed calibrated against governingdocs/backend (cc_mean=6.48, 209 modules), which is:
1. Proprietary (at /Users/hosanglee/governingdocs/backend, not in public repo)
2. Not in published bench/projects.yaml
3. Used only in bench/score_redesign.py as GUINEA_PIG for post-hoc empirics

The divisor decision (commit a15f5d9) explicitly cites this project as "the motivating real-world case" for the v0.20→v0.23 change.

SCORING.md (lines 285-291) does NOT disclose this proprietary dependency - it only refers to "realistic backends" generically.

Suggested remediation is sound: either (1) add a public synthetic/placeholder validator/parser-heavy test case to bench/projects.yaml to make calibration reproducible, or (2) explicitly document in SCORING.md that the /8 divisor was calibrated against governingdocs/backend and is not fully reproducible from published materials.

The empirics study documentation (SCORE_SHAPE_REDESIGN_EMPIRICS.md) does acknowledge the 28-project bench including governingdocs/backend, but this is research documentation, not user-facing.

### [major] Claimed independence axiom contradicted by empirical correlations documented as moderate  (confirmed)
- **loc**: docs/SCORING.md lines 34-38 and 403-434
- **claim**: Design goal #1 asserts 'The five sub-metrics are chosen so that improving one does not mechanically improve the others.' The empirical axis independence section then reports two of ten pairwise correlations at 'moderate' coupling (|r| ≥ 0.5): modularity↔depth at -0.617 and acyclicity↔depth at -0.581.
- **evidence**: The document's own candid observation (lines 464-467) acknowledges the design language 'was stronger than the data supports' and notes 'the empirical study... concluded against any axis change: [candidates] all shake the project leaderboard substantially... the honest reading the empirics support is twofold: (1) the two moderate pairs both involve `depth`, and under every tested aggregator `depth` correlates only weakly with `overall`.'
- **impact**: A developer reading design goals #1 expects true independence. The pairwise correlations mean the axes are not independent. The weak *leverage* on overall (the true fix) is not explained in the design goals section, only buried in the empirical analysis section.
- **fix**: Reframe design goal #1 to distinguish 'statistical orthogonality' (which is not achieved) from 'operational independence on the score' (which is achieved for 4/5 axes). Or simplify to: 'The five sub-metrics capture distinct structural pathologies. Two involve depth and are moderately correlated with other axes; the other three are decoupled.'
- **notes**: The ticket should capture: (1) The design goals section makes an independence claim unsupported by empirical data. (2) The mitigation exists but is not explained in the design goals section—only in the empirical analysis section (lines 476-486). (3) The suggested action in the finding is reasonable: distinguish between statistical orthogonality (not achieved) and operational independence on the score (achieved for 4/5 axes). (4) The document does honestly acknowledge the gap (line 466: "was stronger than the data supports"), showing this is a known-but-unresolved documentation issue, not a hidden problem. (5) The two moderate correlations both involve depth, and the weak leverage of depth on overall (|r| ≤ 0.187) is the saving grace, but this explanation must be moved into or referenced from the design goals section itself to match developer expectations.

### [minor] Small-project threshold (20 functions) for complexity axis not empirically justified  (confirmed)
- **loc**: docs/SCORING.md line 274; src/archy/score.py line 272
- **claim**: Projects with fewer than 20 functions return complexity=1.0 (perfect score). The justification is that 'cc_mean is statistically unstable on tiny inputs and one branchy dispatcher can dominate the mean.'
- **evidence**: The research documents do not contain a sensitivity analysis showing how complexity scores vary with different thresholds, or statistical power calculations justifying 20 as the breakpoint where cc_mean becomes 'stable.'
- **impact**: Projects with 19 functions get a vaccuous 1.0; projects with 20 get actual measurements. This creates a cliff at the boundary. A project with exactly 20 functions that adds one utility function sees complexity drop sharply if cc_mean is poor, even though the architecture may not have changed substantively.
- **fix**: Either: (1) provide empirical sensitivity analysis showing why 20 is the right threshold, or (2) use a softer transition (e.g., linear interpolation from 1.0 to measured value in the [15, 25] range) rather than a hard cliff.
- **notes**: The ticket should capture: (1) The specific threshold value (20) appears hard-coded in one constant and is not tunable or configurable. (2) No existing projects in the 28-project benchmark fall in the [15, 30] range to validate the choice empirically. (3) The justification appeals to statistical instability but provides no: (a) variance measurements of cc_mean at different function counts, (b) bootstrap confidence intervals, (c) cross-validation of stability metrics, or (d) sensitivity sweep showing cc_mean behavior in [10, 50] function range. (4) Suggested mitigations: (a) empirical analysis of cc_mean variance using synthetic or real projects with 10-50 functions, (b) soft transition via interpolation as proposed in the finding (linear blend from 1.0 at 15 functions to the measured value at 25), or (c) configuration knob in archy.yaml to let users tune for their codebase. (5) The finding's proposal for linear interpolation in [15, 25] is reasonable and would surface the edge case (project adding one utility function causing complexity score drop if cc_mean was poor) as a smooth gradient rather than a cliff.

### [minor] Modularity normalization clamp choice not validated  (confirmed)
- **loc**: docs/SCORING.md lines 78-84
- **claim**: Newman's Q is normalized as `clamp01((Q + 0.5) / 1.5)`, mapping the canonical [-0.5, 1.0] range onto [0, 1]. The formula is presented as a straightforward linear map with no explanation for the choice of parameters (0.5 offset, 1.5 divisor).
- **evidence**: The document cites sentrux's quality-signal-design.md as the source and states 'archy adopted sentrux's normalization explicitly so cross-tool numbers stay comparable.' This is borrowed from another tool, not validated independently on the 28-project benchmark. No sensitivity analysis tests whether alternative normalizations (e.g., (Q + 0.4) / 1.4, or sigmoid-based) would change project rankings or correlations.
- **impact**: The choice is not documented as empirically validated; it's presented as a principled linear map but is actually a borrowed constant. If the true range of real-world Q values is narrower than [-0.5, 1.0], the mapping wastes dynamic range.
- **fix**: Document that this normalization is inherited from sentrux for cross-tool compatibility, not independently validated. If validation is desired, run a sensitivity analysis on the 28-project bench using 3-5 alternative normalizations and report whether project rankings or correlation structure change.
- **notes**: The normalization choice is not empirically validated on archy's 28-project benchmark - only acyclicity, depth, and aggregator alternatives were tested per SCORE_SHAPE_REDESIGN_EMPIRICS.md. The formula (Q+0.5)/1.5 maps the theoretical canonical range [-0.5, 1.0] but actual observed Q values span only [0.140, 0.648], resulting in normalized modularity using only [0.427, 0.765] of the [0,1] range. The documentation should clarify: (1) this normalization is inherited from sentrux for cross-tool compatibility, not independently derived; (2) the rationale for adoption could be better explained in SCORING.md lines 78-84; (3) a sensitivity analysis on the 28-project bench testing alternative normalizations (e.g., linear remapping of actual [0.14, 0.65] range, or sigmoid-based approaches) would help determine if dynamic range is being wasted operationally or if the current choice is justified by stability considerations.

### [minor] Depth axis formula midpoint choice (/8) not empirically grounded  (confirmed)
- **loc**: docs/SCORING.md lines 182-187
- **claim**: Depth score is computed as `1 / (1 + max_depth / 8)`, where '8 is a tunable taste choice inherited from sentrux: a chain of 8 modules gives a depth score of 0.5.' The claim is that this choice was 'inherited from sentrux' without independent validation.
- **evidence**: The document explicitly frames the 8-module midpoint as 'inherited from sentrux' and a 'tunable taste choice.' Unlike the complexity divisor, there is no empirical study showing that 8 is the right cutoff. The SCORE_SHAPE_REDESIGN_EMPIRICS tested three depth reformulations but did not vary the midpoint itself (they tested `depth_with_scc_penalty`, `depth_size_relative`, etc., but kept the /8 divisor).
- **impact**: Two magic numbers in the depth formula (8-module midpoint) and complexity formula (/8 divisor) are presented as independent choices, but the document admits depth's is 'inherited from sentrux' and complexity's is justified by one private project. Neither is independently validated on the public benchmark.
- **fix**: Document that depth's /8 is a borrowed constant from sentrux, not validated on the 28-project benchmark. If validation is desired, run a brief sensitivity analysis (e.g., test /4, /6, /8, /10, /12) on the public bench and report whether ranking is stable.
- **notes**: The claim is well-founded. Key evidence: (1) docs/SCORING.md lines 185-186 explicitly state the /8 is "inherited from sentrux" and a "tunable taste choice"; (2) the 2026-05 SCORE_SHAPE_REDESIGN_EMPIRICS study (the most comprehensive validation effort on the 28-project benchmark) tested three depth *reformulations* but did NOT test sensitivity of the /8 divisor itself via a comparison like /4, /6, /8, /10, /12; (3) complexity's /8 divisor (line 261, same filename) was explicitly calibrated empirically ("widened from /5 to /8 in v0.23 after the original calibration drove the geomean to 0.000" - line 285-290), whereas depth's /8 lacks that same level of documented empirical justification on archy's benchmark. The ticket should capture: (a) the /8 depth divisor is documented as borrowed from sentrux and not independently validated, (b) unlike the complexity axis's /8 (which has documented calibration), the depth /8 has no equivalent empirical grounding in archy's research, (c) a sensitivity analysis (testing /4, /6, /8, /10, /12 on the 28-project benchmark and reporting if project ranking is stable) would be the natural follow-up if more rigor is desired, though the current framing is honest about the limitation.

### [minor] Complexity axis promotion claim (v0.20) lacks ablation evidence for the five-axis choice  (confirmed)
- **loc**: docs/SCORING.md lines 12-22; docs/research/RESEARCH_METRICS.md section 17
- **claim**: The document states complexity was 'promoted from a v0.17 diagnostic to a score axis in v0.20 after the then-27-project benchmark showed it is the most orthogonal signal archy has ever measured against the existing four (max `|r| = 0.197`).' This is presented as the sole justification for promoting it to the fifth axis.
- **evidence**: The promotion is justified by orthogonality to the four existing axes, but no ablation study compares whether a five-axis geomean with complexity is better than a four-axis geomean plus complexity as a separate diagnostic. The document does not show whether users or projects benefit from the fifth axis being in the aggregate rather than alongside the four.
- **impact**: The complexity axis was added because it is orthogonal, but orthogonality alone is not grounds for inclusion in a composite score. OECD guidance requires showing that the new indicator adds discriminative value beyond existing ones. The document does not show case studies or ranking changes that would justify the geometric mean now including complexity.
- **fix**: Add empirical evidence: (a) show project ranks under four-axis vs five-axis geomean and explain whether the shift is meaningful, (b) show whether the addition of complexity creates more stable or less stable project comparisons over time, (c) cite external evidence (if any) that complexity belongs in a code-quality composite.
- **notes**: The ticket should capture: (1) The promotion decision in v0.20 was justified by orthogonality alone, not by empirical comparison of geometric-mean outputs. (2) The OECD four-gate framework (independence, directionality, actionability, discriminant validity) applies conceptually to complexity but was not invoked explicitly in v0.20 docs—it appears in AXIS_REVIEW.md (2026-05) for call-density rejection, showing the framework was adopted afterward. (3) Missing evidence: (a) project rank comparison (4-axis vs 5-axis geomean on the 27-28-project bench), (b) whether the complexity score introduces volatility in trending (archy trend over time), (c) whether users perceive the fifth axis as adding signal or noise. (4) Suggested fix: Add a comparative ranking table showing how projects rank under 4-axis vs 5-axis, compute Spearman rank stability, and cite external evidence on whether McCabe complexity belongs in code-quality composites (the documents cite McCabe defect-correlation studies; a composite-specific citation would strengthen the case). (5) Note: The complexity axis is likely defensible (it is orthogonal, actionable, and measures something real), but the promotion decision documentation sets the bar at orthogonality when the OECD Handbook (which the docs cite elsewhere) requires all four gates to pass.

### [minor] Call-weighted Q rejection rationale cites 'directionality' gate without defining it  (confirmed)
- **loc**: docs/SCORING.md line 118 (reference to CALL_WEIGHTED_Q_EMPIRICS.md); docs/research/CALL_WEIGHTED_Q_EMPIRICS.md
- **claim**: The document states call-weighted Q was kept as a diagnostic rather than promoted to an axis because of '[`CALL_WEIGHTED_Q_EMPIRICS.md`](research/CALL_WEIGHTED_Q_EMPIRICS.md)' which decided against 'ship as axis replacement' on 'directionality, actionability, and discriminant-validity grounds.'
- **evidence**: The reference to 'directionality' is cited but never defined or explained in the SCORING.md document. The reader is forced to open a separate research document to understand what 'directionality' means as a gate criterion. The OECD handbook uses 'directionality' to mean 'the indicator should increase with the desired outcome' (or decrease consistently), but this is not explained here.
- **impact**: The rejection of call-weighted Q is presented as principled but not transparent to readers of SCORING.md. A user cannot evaluate whether the decision was well-grounded without reading a separate detailed research doc.
- **fix**: Add a one-sentence gloss: 'directionality' means the indicator should move consistently in one direction as the underlying system property improves; call-weighted Q fails because it can increase or decrease depending on the call-graph shape, making it ambiguous as a scoring signal.'
- **notes**: The suggested gloss is close but not identical to the formal OECD definition given in AXIS_REVIEW.md. The OECD definition is: "There must be a defensible answer to 'is higher better, or worse?' that holds across the population." The suggested gloss focuses on "moving consistently in one direction as the underlying system property improves," which is narrower. A complete fix should either: (1) use the formal OECD definition from AXIS_REVIEW.md, or (2) add a parenthetical explaining what the three gates (directionality, actionability, discriminant-validity) mean as a trio of OECD composite-indicator criteria. The current reference at line 665-666 links to AXIS_REVIEW.md but provides no inline definition, making SCORING.md incomplete as a standalone reference. Also note that line 118 in CALL_WEIGHTED_Q_EMPIRICS.md (referenced from SCORING.md) does NOT cite 'directionality' — the reference appears to be off by several sections. The actual rejection rationale for call-weighted Q as a replacement axis appears in CALL_WEIGHTED_Q_EMPIRICS.md section "Why the empirics don't justify replacing unweighted Q" (lines 89-97), which explicitly discusses directionality but defines it by example rather than formally.

### [nit] Benchmark diversity claim not quantified  (confirmed)
- **loc**: docs/SCORING.md lines 500-504
- **claim**: The benchmark is described as 'spanning small CLI tools (click, msgspec) to very large frameworks (pytorch at 2,252 modules, django, numpy, sqlalchemy, dagster), with diversity across web / async / scientific / ML / ORM / plugin-host / devops / workflow-orchestration / build-tooling / syntax-highlighting / generated-SDK domains.'
- **evidence**: The diversity is asserted by listing project names and domains but never quantified. Is the distribution uniform across these domains, or are some overrepresented? The 28-project list clusters around web frameworks (starlette, anyio, scrapy, fastapi, django, flask, aiohttp, etc.) with several async-related projects, but only one genuine ML/scientific project (pytorch), one pure scientific (numpy), and one ORM (sqlalchemy). The 'diversity' claim is not backed by a table showing the domain breakdown or justifying why 28 projects is enough.
- **impact**: A user cannot assess whether the 28-project benchmark is truly representative of the kinds of Python projects they are analyzing. The document overstates diversity by listing domains without showing their actual representation.
- **fix**: Add a table showing project count per domain (e.g., web frameworks: 8/28, async: 4/28, ML/scientific: 2/28) and justify the 28-project size as statistically adequate for deriving thresholds (reference a power analysis or cite external precedent).
- **notes**: The diversity claim is not factually wrong but operationally incomplete. Ticket should request: (1) A domain breakdown table showing project count per category (web frameworks: 9/28, async: included in web count, scientific: 3/28, ML: 1/28, etc.); (2) A footnote acknowledging that web/HTTP clients dominate the benchmark and explaining why this is acceptable for archy's use case; (3) If auto-generated SDKs (boto3/botocore, 2/28) are kept, a caveat noting they represent only one code-generation pattern; (4) Reference to the specific power analysis or citation justifying the 28-project size, or removal of the implication that size is statistically validated. The document is written to high standards of rigor - meeting those standards here would strengthen rather than undermine the credibility of the benchmark.

## research / SIMULATE_ORACLE


### [major] Critical tautology not fully eliminated: clean samples still gate on identical edge sets, making topology-derived metrics agree by construction  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:55-64 and bench/simulate_oracle.py:201, 211-213
- **claim**: The 315/315 oracle match on clean samples proves simulate's machinery and lines=() synthetic edges work correctly. But the bench explicitly gates on `clean = _edges(g1) == want_edges` (line 201), then only counts clean samples as matches (line 213). This means the oracle compares two topologically-identical graphs.
- **evidence**: In bench/simulate_oracle.py line 201, a sample is marked 'clean' if and only if the actual written graph edges exactly equal the intended delta. Lines 211-213 show that only when `clean==True` is the match counted toward the 315/315 oracle stat. The empirics doc (line 56) admits 'the two graphs are topologically identical' on clean samples, line 57 states 'the topology-derived fields *must* agree.' By construction, if two graphs have identical topology, their cycles (frozenset of modules), violations (keys exclude lines), score deltas (pure functions of topology), back-edges (DSM ordering on identical graphs), and propagation costs (reachability on identical structure) will match. No independent validation occurs.
- **impact**: The 315/315 figure measures implementation consistency of the delta-application and graph-copy machinery, not whether simulate genuinely predicts consequences of an unwritten import. If simulate has a subtle bug in how it applies edges or computes fields, this oracle would not catch it—only mutations that change the topology differently than intended would surface. An agent cannot distinguish between 'simulate correctly predicts the delta' and 'simulate applies the delta consistently to an in-memory copy.'
- **fix**: Clarify or restructure the oracle claim: either (a) rename 315/315 to 'consistency match' and acknowledge it only validates implementation, not prediction; or (b) design an adversarial oracle that compares simulate's output on an intentionally-perturbed delta against the graph that would result from writing the unperturbed delta, forcing the two code paths to diverge and testing whether divergence is caught.
- **notes**: The finding is technically correct but the issue is partially mitigated: (1) The documentation at SIMULATE_ORACLE_EMPIRICS.md lines 55-64 already explicitly acknowledges the tautology with a dedicated section "On the precision of '315/315.'" It states the oracle validates "implementation consistency" not prediction correctness, and explicitly says it is "not evidence that simulate predicts something an oracle on identical graphs couldn't." (2) The separate 96% fidelity metric is presented as "the separate measure of how often the agent's intended delta *is* that graph," which actually addresses the core concern. (3) The oracle DID prove useful by catching a real bug (self-loop handling) during adversarial review, demonstrating value despite the tautology. HOWEVER, (4) The results presentation could be significantly clearer: the line "Oracle on clean samples: 315/315 matched. This is the real correctness gate" could mislead readers into thinking this validates prediction correctness rather than implementation consistency. The summary line "oracle holds exactly (308/308)" appears outdated compared to "315/315." Recommendation: (a) Clarify the results presentation to explicitly state "Implementation consistency check" not "correctness gate," (b) Add a callout to the precision section in the results summary, (c) Fix the 308/308 vs 315/315 discrepancy, (d) Consider adding a note explaining why the oracle is still valuable despite the tautology (it caught a real bug in self-loop handling). The claim itself about the tautology is correct and represents a real methodological limitation of the current oracle, though it's already documented.

### [major] Fidelity rate (96%) conflates agent-facing intent with implementation correctness, masking a real user problem  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:34, 61-62, and bench/simulate_oracle.py:199-220
- **claim**: The 96% fidelity (315/327 clean) is presented as the measure of 'how often an agent's intended single-edge delta maps 1:1 to the written import.' The document frames this as the separate, honest measure (line 61-62). But the 315/315 oracle is then claimed to validate simulate on the 96%-of-the-time case where the intent and reality align. The remaining 4% dirty samples are dismissed as 'ancestor-package edges,' a documented limitation. However, this splits the problem in a way that lets simulate claim correctness only on cases where its input happens to match reality.
- **evidence**: The bench splits samples into clean (agent's intent matches written reality, 315/327) and dirty (mismatch, 12/327). The oracle (315/315) is reported only on clean samples. Dirty samples are not required to match the oracle—line 6 of results file states 'Oracle on dirty samples: 0/12 matched.' This is reasonable (simulate's input was wrong), but it means the published 315/315 correctness claim is conditional on having a corpus where 96% of random single-edge deltas happen to write as one import statement. If the user corpus were different (e.g., more re-exports, more complex ancestor patterns), the oracle would only apply to a smaller fraction of real queries. The fidelity rate + oracle form a logical circle: the oracle is strong only on the 96% of cases where intent matches reality; on the 4% where it doesn't, simulate isn't tested.
- **impact**: An agent using simulate may unconsciously over-trust the 315/315 figure and apply simulate to a wide range of deltas. When an ancestor-package edge causes the written graph to differ from the simulated one, the agent cannot rely on the 315/315 oracle to catch it. The documentation says this is handled ('documented, quantified, and surfaced in the tool description'), but an agent that simulates before writing will discover the mismatch after the file is already modified, not before.
- **fix**: Reframe the fidelity + oracle split: make it clear that 'the oracle validates simulate only under the precondition that the written import matches the intended delta (96% of corpus cases).' Alternatively, measure oracle correctness on dirty samples too: compute how far off simulate's predictions are when the written delta diverges. This would quantify the risk an agent faces when fidelity fails.
- **notes**: ISSUE CONFIRMED: Documentation clarity gap, not an implementation bug. The current documentation (line 61-62 of SIMULATE_ORACLE_EMPIRICS.md) does explicitly separate fidelity from oracle, but more prominence/clarity is needed.

KEY FACTUAL FINDINGS:
- 315/315 oracle is correctly reported only on clean samples (where intended delta matches written graph)
- Dirty samples (12 cases, 4% of corpus) show simulate diverges when agent omits ancestor-package edges
- Documentation does warn agents to include ancestor edges in tool description (src/archy/mcp.py) and AGENT_LOOP.md
- The warning is clear ("include those edges to model it exactly") but could be higher-priority

SCOPE TO CAPTURE:
1. Agents who don't include ancestor edges will write code before discovering in archy_diff that additional edges were created
2. The 315/315 figure in results table is prominent; could be misread as general correctness rate rather than conditional (clean-samples-only) rate  
3. Need empirical data on agent behavior: do agents actually follow the ancestor-edge guidance?
4. Suggested fix: Make the precondition even more explicit in results table and tool description - something like "Oracle on clean samples (where intent matched written): 315/315"
5. The applied.added_edges return value echoes back what was actually applied, which could help agents detect under-specified deltas, but this feature may not be widely known/used
6. Consider adding a note in mcp.py tool description about what happens if you omit ancestor edges and then run archy_diff (you'll see extra edges that simulate didn't predict)

SEVERITY: Major (agents can be misled into write-before-discover workflow) but not blocker (documentation guidance exists, just needs clarification)

### [major] Corpus size and diversity too small to support 96%-plus confidence claims without stratification analysis  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:95, and bench/simulate_oracle_results.md:12-23
- **claim**: The corpus comprises 11 real repos totaling 174 modules (largest: scrapy with 174 nodes). Samples are ~15 per repo per kind (removal/addition), yielding 327 deltas total. The empirics state fidelity is 96% and the oracle matches 315/315. But 327 samples across 11 repos of widely varying size and structure is insufficient to claim 96% fidelity for 'single-edge deltas' broadly, especially without stratification by module count, depth, or cycle density.
- **evidence**: bench/simulate_oracle_results.md table shows modules per repo: scrapy=174, pydantic=104, rich=100, datasette=68, mkdocs=61, fastapi=48, starlette=34, flask=24, httpx=23, requests=19, click=17. Total ~674 modules across 11 repos; 327 samples = 48% of edges sampled across the corpus, but very unevenly. The bench uses _stride to sample ~15 of every edge/pair, which under-samples small repos. The 12 dirty samples come from 8 repos (datasette=3, mkdocs=2, pydantic=1, others=6), but each repo contributes only ~30 samples. This is too small to claim fidelity by repo or to detect if certain structures (e.g., deep hierarchies, high cycle density) have different fidelity rates.
- **impact**: If a user's codebase has a different structure than the 11-repo average (e.g., deeper package nesting, more re-export patterns), the 96% fidelity claim may not hold. An agent relying on the 96% figure without knowing the corpus composition might encounter much lower fidelity in their own code and lose confidence in the tool.
- **fix**: Stratify the 327 samples by module count (or depth/density) and report fidelity per stratum. At minimum, report the range of fidelity across repos (currently hidden by aggregation). If a repo has 10/15 clean removes (67%), call that out rather than averaging into 96%.
- **notes**: CONFIRMED: The 96% fidelity claim lacks per-repo stratification and does not explicitly report the range (86.7%-100% for removals, 93.3%-100% for additions). While the underlying mechanism (ancestor-package edges) is explained and quantified at ~4-6% aggregate level, the documents should:

1. Explicitly compute and report per-repo fidelity rates from the table data
2. Highlight that removal fidelity varies from 86.7% to 100% across repos
3. Quantify what percentage of repos achieve 90%+ vs 95%+ vs 100% fidelity
4. Note that the 96% aggregation masks this variation
5. For users working on repos structurally similar to the smaller/larger projects in the corpus, actual fidelity may be lower

The SPEC_SIMULATE.md and tool description (MCP service) both reference "~96% of single-line imports map 1:1" but do not caveat this with per-repo variation or acknowledge that 4 of 11 repos show only 86.7% fidelity for removals.

Suggested fix: Either (a) add per-repo fidelity table to the empirics, (b) state "96% across all samples, with per-repo range 86.7%-100%", or (c) report stratified fidelity by repo size class (small <30 mods, medium 30-100, large >100).

### [major] Performance claim '~1.2x stays flat at scale' rests on a narrow synthetic benchmark unrepresentative of real code  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:95-113, and bench/simulate_oracle.py:266-324
- **claim**: The empirics state 'simulate's overhead over a diff is ~1.2x and stays flat at scale' (line 107), backed by synthetic graphs of 500-10k nodes. But the synthetic graph is 'a sparse near-DAG (out-degree 2, one injected back-edge)' (line 110), and the caveats (line 110-113) admit the ratio 'could rise above 1.2x' on 'dense or heavily-cyclic' graphs and that the flat claim 'is established only for sparse structure.' Yet the bottom line (line 136) restates the flat claim without the qualifier.
- **evidence**: bench/simulate_oracle.py _synthetic function (lines 266-301) builds a graph where each node i has ~2 outgoing edges to random earlier nodes j < i, producing a DAG with out-degree ~2. The added test edge is m0 -> m[n-1], a back-edge in a DAG. Real code often has higher out-degree (e.g., imports from common utilities or packages) and complex cycles (e.g., circular dependencies via __init__ chains). A dense graph (out-degree 5-10) or one with many cycles would spend more time in DSM build and cycle detection, both super-linear. The bench admits this (lines 112-113: 'A density sweep is future work'), but the bottom-line cites the 1.2x ratio as a fact, not a conditional.
- **impact**: An agent or operator deploying simulate on a large, densely-cyclic codebase may see multi-second latencies (as the empirics do state for absolute time), but may not understand why the overhead is higher than 1.2x. If the team later decides to ship simulate in an editor with per-keystroke invocation, the performance assumption may fail on real code.
- **fix**: Restate the performance result as 'on sparse near-DAGs (out-degree ~2) to 10k nodes, overhead is ~1.2x; overhead on dense or heavily-cyclic graphs is uncharacterized.' Do not cite the 1.2x in the bottom line without qualification.
- **notes**: The finding is technically accurate. The ticket should capture:

1. **The communication gap is real:** The bottom-line summary (line 136) omits the critical qualifier that 1.2x applies only to sparse structures. Readers skipping the detailed findings will not understand the scope limitation.

2. **The measured data supports the claim for the tested case:** The benchmark correctly shows ~1.2x flatness on the synthetic sparse near-DAG (out-degree ~2) across 500-10k nodes, and the ratios measured in the results file (1.24x, 1.28x, 1.27x, 1.19x) confirm this.

3. **The real risk is accurate:** Dense or heavily-cyclic real-world graphs (common in large Python codebases with circular dependencies via __init__ chains, or modules importing from central registries) could plausibly exhibit super-linear DSM/cycle-detection cost, causing the ratio to exceed 1.2x. The benchmark doesn't measure this.

4. **Suggested fix:** Reword line 136 to: "simulate is cheap relative to a diff (~1.2x) on sparse graphs at every scale tested (500-10k nodes); overhead on dense or heavily-cyclic code is uncharacterized and could exceed this baseline." Or add a cross-reference to the caveat in line 110-113.

5. **Phase 2 recommendation:** Flag "density sweep" (line 113) as a higher priority than currently scheduled, since the undefined case directly impacts deployment confidence on real codebases.

### [minor] Ancestor-package edge characterization incomplete: 4% to 6% discrepancy between versions and no analysis of when it strikes  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:88, and bench/simulate_oracle_results.md:25-37
- **claim**: The empirics state '~4% of single-line import edits in the corpus touch more than one graph edge' (line 88), but the original version said '~6%' (git show 3a789cc says '~6%'). The dirty sample count is 12/327 = 3.7%, which rounds to ~4%, but this is presented as a known limitation ('ancestor packages') without analyzing when it occurs. The 12 examples show mostly submodule-removal and submodule-addition cases, but no systematic characterization of whether it depends on depth, package structure, or re-export count.
- **evidence**: The bench marks a sample dirty if the real graph edges don't exactly match the intended delta. All 12 dirty samples in the results are ancestor-package cases (submodule imports pulling in parent packages, or multiple imports on one line). But there's no breakdown: e.g., 'of 190 submodule removals, 7 touched ancestor edges' or 'of 137 submodule additions, 5 touched ancestor edges.' Without this stratification, the claim '~4%' is a bare aggregate; an agent cannot estimate whether their next delta is likely to be clean.
- **impact**: An agent cannot predict when the fidelity caveat will bite. If the team later reports 'clean rate was 92% in December, now 88% in January,' it will be unclear whether the corpus changed or the analysis was too coarse to detect a real shift.
- **fix**: Break down the 12 dirty samples by (1) kind (remove vs add), (2) depth of target (a.b.c vs a.b), (3) whether the import was a top-level or nested statement. This enables an agent to estimate fidelity for their planned delta before writing it.
- **notes**: CONFIRMED ISSUES:

1. **Version discrepancy explanation incomplete**: The change from 6% to 4% is explained in commit 49431d6 but users reading only the current docs/research/SIMULATE_ORACLE_EMPIRICS.md will not understand why the original ~6% claim became ~4%. The note about "skipping qualnames that are not valid dotted identifiers" (line 89-90) documents part of it, but the context that this was an adversarial-review fix tightening the oracle is not in the empirics doc itself.

2. **Dirty sample characterization is incomplete per the finding**:
   - The 12 samples ARE listed (bench/simulate_oracle_results.md lines 25-37) with full edge info
   - But NO breakdown by (kind, depth, statement-position) is provided
   - Examples of missing analysis:
     - "Of 11 removals, 10 are ancestor-package cases (both ancestor+child), 1 is direct"
     - "Of 1 addition, 1 touches ancestor edges (flask.json.tag -> flask)"
     - Depth stratification: "Of 8 depth-3 imports: 4 ancestor+child, 2 ancestor+extra, 2 multiple"
   - This matters for predictive value: an agent cannot estimate "for my delta, how likely is ~4% to bite?"

3. **Impact on trend detection is real but speculative**:
   - The finding's concern ("if clean rate drops from 92% to 88%, we won't know if corpus shifted") is theoretically valid
   - However, the tool is still new (shipped in v0.29.0, empirics validated 2026-06-02), so no trend data exists yet to validate whether the coarse ~4% is insufficient for real use
   - Ticket should note that if trend-watching is deployed, stratification becomes load-bearing

ACTION ITEMS FOR TICKET:
1. Add stratification breakdown of the 12 dirty samples to bench/simulate_oracle_results.md (or a separate analysis section in docs/research/SIMULATE_ORACLE_EMPIRICS.md):
   - By operation (rm vs add)
   - By target depth (count per depth level)
   - By pattern (ancestor+child, ancestor+extra, multiple, direct)
2. Document the oracle tightening (unicode sample removal, import form change) in empirics doc as context for the 6%→4% change
3. Track whether trend reporting is added later; if yes, revisit stratification for actionability before shipping trend monitoring

### [minor] Layer-violation coverage relies on unit test and synthetic smoke, not real-world rule evaluation  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:120-127, and bench/simulate_oracle.py:327-354
- **claim**: The empirics state 'the violation path is covered' by a synthetic 4-layer smoke test (forbid l0->l1) plus the unit test test_added_layer_violation_is_surfaced. The smoke test runs once per bench and checks only that a forbidden edge flags a violation and an allowed edge stays silent. But this is narrow: (1) the corpus carries no archy.yaml, so no real-world layer rules are tested, (2) the smoke test is deterministic and brittle (one fixed config), (3) the oracle (_matches function, line 108-125) only compares violation-set keys, not the risk or severity of violations.
- **evidence**: bench/simulate_oracle.py _violation_smoke (lines 327-354) creates a hardcoded 4-layer graph with one forbid rule. It runs once, not per sample, so it doesn't measure how often violations are correctly predicted on the real-repo corpus. The _matches function (line 119) compares _violation_keys(sim.violations) == _violation_keys(real.violations), where _violation_keys extracts (from_layer, to_layer, source, target) and ignores risk or other violation metadata. If simulate produces the correct set of violations but mis-scores their risk or mis-classifies their category, _matches would not catch it.
- **impact**: A user with real layer rules (e.g., a 6-layer architecture with dozens of forbid rules) cannot be confident that simulate will correctly predict violations. The synthetic smoke is a sanity check (yes, the forbid rule engine runs), not a validation that it works on complex real-world configs.
- **fix**: Add a real-world layer-rule smoke test using one of the 11 repos that does have an archy.yaml, or create a synthetic 6-layer config with multiple rules and test it. Alternatively, acknowledge in the bottom line that layer-violation correctness is asserted, not empirically validated on real configs.
- **notes**: The finding correctly identifies that layer-violation coverage relies on synthetic smoke test (4-layer, 1 forbid rule) rather than empirical validation on real-world complex layer configs. However: (1) The code explicitly acknowledges this gap (simulate_oracle.py:469-474); (2) The claim about unchecked "risk/severity" is misleading - these fields don't exist on Violation, only on the summary which is excluded by design; (3) The oracle correctly validates that simulate matches diff on 315 real samples using the identical find_violations function; (4) The suggested action (use real archy.yaml from corpus) is infeasible - all 11 corpus repos have zero archy.yaml. A valid improvement would be to extend the smoke test from 4 layers/1 rule to 6+ layers/multiple forbid rules to better cover complex real-world scenarios. Current risk: future complex layer configs might expose edge cases not covered by the narrow synthetic test.

### [nit] Dirty-sample oracle claim '0/12 matched' is misleading: dirty samples are expected to diverge, so the statistic proves nothing  (confirmed)
- **loc**: docs/research/SIMULATE_ORACLE_EMPIRICS.md:36, and bench/simulate_oracle_results.md:6
- **claim**: The results state 'Oracle on dirty samples: 0/12 matched' (line 36 of empirics, line 6 of results). This is presented as a finding ('every divergence explained, below'), but it is circular: a dirty sample is defined as one where the written delta differs from the intended delta, so of course the oracle (which compares intended-delta graph vs written-delta graph) will diverge. Reporting this as '0/12 matched' suggests the oracle was tested and passed/failed; in reality, dirty samples were filtered out of the oracle test by definition.
- **evidence**: bench/simulate_oracle.py line 201 defines clean = (_edges(g1) == want_edges). Line 211-213 show that only clean samples contribute to the oracle match count. Line 216-217 handle dirty samples separately (bucket[3] counts dirty matches, but these are never included in the 315/315 figure or reported as part of the oracle correctness). The dirty samples are characterized in lines 219-220 as 'real touched [...]', describing what the actual delta touched, not testing the oracle.
- **impact**: A reader may misinterpret '0/12 matched' as 'we tested simulate on 12 cases where it was wrong and it was wrong all 12 times,' when in fact the 12 cases were pre-filtered as ones we don't test the oracle on. This is honest (the document later explains ancestor packages), but the framing is confusing.
- **fix**: Remove the '0/12 matched' line or rephrase it to 'Oracle not tested on dirty samples (by design: their deltas did not match intent). Dirty-sample characterization: ...' to make clear that the 12 examples are post-hoc analysis, not failed oracle tests.
- **notes**: The concern is primarily about presentation clarity, not factual accuracy. The docs ARE honest about why dirty samples diverge, but the table format (line 36 of EMPIRICS.md) presents "Oracle on dirty samples: 0/12" without explicit framing that this is an expected, characterized divergence, not a test that was run and failed. Consider: (1) adding a note in the table that dirty-sample divergence is expected and quantified below, or (2) moving the 0/12 line outside the main results table to the "Fidelity gap" explanation section where it belongs logically. The suggested rewording in the finding is reasonable but the current docs are not actually incorrect—just potentially unclear for readers who skip the methodology section.

## research / TYPE_HINT_COVERAGE


### [minor] False inter-axis median benchmark: 'well above ~0.45' contradicted by actual data showing 0.371  (confirmed)
- **loc**: TYPE_HINT_COVERAGE_EMPIRICS.md line 38; AXIS_REVIEW.md line 54; bench/results.md lines 41-52
- **claim**: Both documents state type-hint coverage correlations are 'well above the inter-axis median' (~0.45), yet the actual inter-axis median from the 27-project bench is 0.371, not 0.45. This misquotes the baseline and makes coverage sound worse than it is (1.5x the actual median, not 1.2x).
- **evidence**: TYPE_HINT_COVERAGE_EMPIRICS.md line 38: 'well above the inter-axis median for any existing pair.' AXIS_REVIEW.md line 54: 'median |r| ~ 0.45'. Actual bench data from results.md: 10 inter-axis pairs with |r| = [0.383, 0.617, 0.389, 0.110, 0.581, 0.458, 0.068, 0.359, 0.052, 0.159], median = 0.371.
- **impact**: The 0.45 figure is either a typo or misreading. Using 0.45 instead of 0.371 understates how much coverage correlations exceed baseline, making them sound worse than they are. However, this is minor because 0.551 is still within the OECD safe zone (<0.7). The claim 'well above median' is true but the quantified benchmark is wrong by 21%.
- **fix**: Correct the inter-axis median to 0.371 in both documents. The conclusion remains (type-hint above baseline), but with accurate numbers.
- **notes**: This is a genuine factual error in documentation, not a methodological flaw. AXIS_REVIEW.md line 54 cites "median |r| ~ 0.45" when the correct value is 0.371 based on the 27-project benchmark data in bench/results.md (captured 2026-05-18). TYPE_HINT_COVERAGE_EMPIRICS.md references the same median without explicitly stating a number, so it inherits the misquotation through cross-reference.

The 10 inter-axis Pearson correlations are: modularity-acyclicity (0.383), modularity-depth (0.617), modularity-equality (0.389), modularity-complexity (0.110), acyclicity-depth (0.581), acyclicity-equality (0.458), acyclicity-complexity (0.068), depth-equality (0.359), depth-complexity (0.052), equality-complexity (0.159). Median = 0.371.

Suggested fix: Update AXIS_REVIEW.md line 54 from "median |r| ~ 0.45" to "median |r| ~ 0.37" or "median |r| = 0.371". Update any other references to this baseline in TYPE_HINT_COVERAGE_EMPIRICS.md or related documents. The conclusion that type-hint coverage (max 0.551) exceeds the inter-axis median remains valid and is stronger than the incorrect 0.45 baseline suggested.