from src.pipelines.utils.kg_revision_limits import (
    apply_disable_kg_revisions,
    ensure_kg_norev,
    kg_agent_attempt_limit,
)


def test_apply_disable_kg_revisions_is_noop_when_flag_off() -> None:
    config = {"kg_hint_revision_max_attempts": 2}
    assert apply_disable_kg_revisions(config)["kg_hint_revision_max_attempts"] == 2


def test_apply_disable_kg_revisions_zeros_kg_loops_only() -> None:
    updated = apply_disable_kg_revisions(
        {
            "disable_kg_revisions": True,
            "kg_hint_revision_max_attempts": 2,
            "post_publish_structural_retries": 2,
            "kg_max_attempts": 4,
        }
    )
    assert updated["kg_max_attempts"] == 1
    assert updated["kg_hint_revision_max_attempts"] == 0
    assert updated["post_publish_structural_retries"] == 0
    assert updated["continuity_audit_retries"] == 0
    assert updated["continuity_audit"]["enabled"] is False
    assert updated["presence_coverage_audit"]["enabled"] is False


def test_ensure_kg_norev_defaults_without_overriding_explicit_off() -> None:
    enabled = ensure_kg_norev({}, default=True)
    assert enabled["disable_kg_revisions"] is True
    assert kg_agent_attempt_limit(enabled) == 1

    explicit_off = ensure_kg_norev(
        {"disable_kg_revisions": False, "kg_max_attempts": 3},
        default=True,
    )
    assert explicit_off["disable_kg_revisions"] is False
    assert kg_agent_attempt_limit(explicit_off) == 3


def test_kg_agent_attempt_limit_defaults_to_one() -> None:
    assert kg_agent_attempt_limit({}) == 1
    assert kg_agent_attempt_limit({"kg_max_attempts": 4}) == 4
    assert kg_agent_attempt_limit({"disable_kg_revisions": True, "kg_max_attempts": 4}) == 1
