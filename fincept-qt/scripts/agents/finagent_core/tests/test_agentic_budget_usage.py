from types import SimpleNamespace

from finagent_core.agentic.budget import BudgetGuard


def test_budget_snapshot_preserves_input_output_and_cache_tokens():
    guard = BudgetGuard.from_config({"model": {"model_id": "deepseek-flash"}})

    response = SimpleNamespace(
        metrics={
            "input_tokens": 900,
            "output_tokens": 300,
            "cache_read_tokens": 500,
        },
        content="ignored",
    )
    guard.record_llm_call("prompt", response)

    snapshot = guard.snapshot()
    assert snapshot["tokens_used"] == 1200
    assert snapshot["prompt_tokens"] == 900
    assert snapshot["completion_tokens"] == 300
    assert snapshot["cached_prompt_tokens"] == 500


def test_budget_usage_details_survive_resume_and_reject_invalid_cache_counts():
    guard = BudgetGuard.from_config({})
    guard.restore_usage(
        {
            "tokens_used": 1200,
            "prompt_tokens": 900,
            "completion_tokens": 300,
            "cached_prompt_tokens": 500,
        }
    )

    response = SimpleNamespace(
        metrics=SimpleNamespace(
            input_tokens=600,
            output_tokens=200,
            cache_read_tokens=900,
        ),
        content="ignored",
    )
    guard.record_llm_call("prompt", response)

    state = guard.usage_state()
    assert state["tokens_used"] == 2000
    assert state["prompt_tokens"] == 1500
    assert state["completion_tokens"] == 500
    # A provider cannot read more cached prompt tokens than it reports as input.
    assert state["cached_prompt_tokens"] == 1100


def test_legacy_budget_restore_keeps_total_accounted_as_input():
    guard = BudgetGuard.from_config({})
    guard.restore_usage({"tokens_used": 1200})

    assert guard.usage_state()["prompt_tokens"] == 1200
    assert guard.usage_state()["completion_tokens"] == 0
