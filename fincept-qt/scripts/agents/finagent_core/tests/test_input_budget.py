from finagent_core.input_budget import apply_output_budget, request_budget_error


def test_complete_agent_request_obeys_turn_input_token_budget():
    # Three UTF-8 wire bytes per token is the same conservative estimate used
    # by the native provider path. Structured tool/config data must count too.
    assert request_budget_error({"query": "ok"}, {"terminal_tools": "x" * 80}, 64) is None
    error = request_budget_error({"query": "ok"}, {"terminal_tools": "x" * 200}, 64)
    assert error is not None
    assert "No provider request was sent" in error


def test_zero_turn_input_budget_is_explicitly_unlimited():
    assert request_budget_error({"query": "x" * 1000}, {}, 0) is None


def test_invalid_turn_input_budget_uses_cost_conscious_default():
    assert request_budget_error({"query": "ok"}, {}, "invalid") is None


def test_imported_agent_model_obeys_turn_output_budget():
    config = {"model": {"max_tokens": 64_000}}
    assert apply_output_budget(config, 12_000) == 12_000
    assert config["model"]["max_tokens"] == 12_000


def test_zero_turn_output_budget_keeps_the_model_ceiling():
    config = {"model": {"max_tokens": 64_000}}
    assert apply_output_budget(config, 0) == 64_000

