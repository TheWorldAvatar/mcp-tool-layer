# Exact Edit shadow evaluation — 2026-07-27

## Scope

This evaluation used an isolated `exact-edits.v1` backend. Production
generation, focused repair, prompt enhancement, and checkpoint replay callers
were not changed.

Both runs replayed their original audited generation checkpoints and reused the
corresponding historical repair focus and full package validation.

## Results

### GPT-5

- Unified-diff baseline: 5 attempts, 0 accepted.
- Baseline mechanical evidence: 18 `git apply` / hunk diagnostics.
- Exact-edit shadow: accepted on attempt 1.
- Full package validation: passed.
- Diff/hunk failures: none.
- Unauthorized, stale-hash, or rollback failures: none.

### GPT-5.2

- Unified-diff control: accepted on attempt 1.
- Exact-edit shadow: attempt 1 rejected; attempt 2 accepted.
- Full package validation: passed.
- Diff/hunk failures: none.
- Unauthorized, stale-hash, or rollback failures: none.

The GPT-5.2 result shows that exact edits do not guarantee first-attempt
semantic success. They remove manual diff-coordinate failure, while behavioral
validation and retry remain necessary.

## Migration gate

The conservative gate is satisfied:

- Neither exact-edit run produced a diff/hunk mechanical failure.
- Neither introduced unauthorized-target, stale-hash, or rollback failure.
- Both models reached full package validity from audited checkpoints.
- GPT-5 improved from 0/5 accepted unified diffs to 1/1 exact-edit success.
- GPT-5.2 remained behaviorally successful, requiring 2 attempts rather than
  its historical control's 1 attempt.

This evidence supports a second-stage migration proposal, but does not itself
switch production callers. A production migration should retain the legacy
unified-diff checkpoint replay path and introduce an explicit rollback/config
switch while new LLM edits adopt exact operations.

## Audit artifacts

- `tmp/exact_shadow_gpt5_20260727/reports/exact_edit_shadow_comparison.json`
- `tmp/exact_shadow_gpt52_20260727/reports/exact_edit_shadow_comparison.json`
- `tests/fixtures/exact_edit_shadow/scenarios.json`
