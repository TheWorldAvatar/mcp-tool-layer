# Exact Edit production migration proposal

The shadow gate passed, but production remains unchanged. A follow-up migration
should be implemented as a separately approved change.

## Proposed rollout

1. Add an explicit editor backend setting with `unified_diff` as the initial
   compatibility default and `exact_edits` as the opt-in path.
2. Switch new LLM generation and repair calls to `exact_edits` in a bounded
   canary mode. Keep legacy `apply_llm_unified_diff` exclusively for historical
   checkpoint replay.
3. Store both canonical `edit_payload` and orchestrator-derived
   `patch_unified_diff` in new patch reports.
4. Replay new reports from exact operations with before/after hash checks.
   Replay old reports from their audited unified diff. Never silently fall back
   from a failed exact payload to its audit diff.
5. Keep validation, best-checkpoint, rollback, target limits, and five patch
   attempts unchanged.
6. Run generation, focused repair, prompt enhancement, and semantic reasoner
   canaries before changing the default backend.

## Rollback

The backend setting must permit immediate return to new unified-diff LLM edits
without changing historical checkpoints. Exact-edit reports already accepted
must remain replayable through their canonical payload.

## Acceptance

- No increase in unauthorized target, stale revision, or rollback failures.
- Lower mechanical edit rejection rate than the unified-diff baseline.
- No reduction in package-validation, mock A-Box, or HermiT pass rates.
- Old checkpoint replay remains byte-compatible.
