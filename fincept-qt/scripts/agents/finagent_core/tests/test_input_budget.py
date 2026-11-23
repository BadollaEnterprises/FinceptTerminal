from finagent_core.input_budget import (
    apply_nested_output_budgets,
    apply_output_budget,
    request_budget_error,
)


def test_registry_agent_card_is_expanded_before_main_request_budget(monkeypatch):
    import io
    import json
    from finagent_core import main as main_module

    payload = {
        "action": "run",
        "config": {"agent_id": "card"},
        "params": {"query": "ok"},
        "input_max_chars": 0,
        "input_max_tokens": 64,
    }
    monkeypatch.setattr(
        main_module,
        "_apply_agent_card",
        lambda config: ({**config, "instructions": "x" * 1000}, "Large card"),
    )
    dispatched = []
    monkeypatch.setattr(main_module, "dispatch_action", lambda *args: dispatched.append(args) or {"success": True})
    monkeypatch.setattr(main_module.sys, "stdin", io.StringIO(json.dumps(payload)))

    result = json.loads(main_module.main([]))

    assert result["success"] is False
    assert "turn-input guardrail" in result["error"]
    assert dispatched == []


def test_registry_agent_card_models_are_clamped_before_dispatch(monkeypatch):
    import io
    import json
    from finagent_core import main as main_module

    payload = {
        "action": "run",
        "config": {"agent_id": "card"},
        "params": {"query": "ok"},
        "input_max_chars": 0,
        "input_max_tokens": 0,
        "output_max_tokens": 12000,
    }
    monkeypatch.setattr(
        main_module,
        "_apply_agent_card",
        lambda config: (
            {**config, "model": {"max_tokens": 64000}, "team": {"model": {"max_tokens": 48000}}},
            "Costly card",
        ),
    )
    captured = {}

    def fake_dispatch(action, api_keys, params, config):
        captured.update(config)
        return {"success": True}

    monkeypatch.setattr(main_module, "dispatch_action", fake_dispatch)
    monkeypatch.setattr(main_module.sys, "stdin", io.StringIO(json.dumps(payload)))

    assert json.loads(main_module.main([]))["success"] is True
    assert captured["model"]["max_tokens"] == 12000
    assert captured["team"]["model"]["max_tokens"] == 12000


def test_agentic_resume_rechecks_stored_config_against_live_input_budget(tmp_path, monkeypatch):
    from finagent_core.agentic.runner import AgenticRunner

    runner = AgenticRunner(api_keys={}, db_path=str(tmp_path / "tasks.db"))
    task_id = runner.state_mgr.create_task(
        "continue",
        {"instructions": "x" * 1000, "model": {"provider": "deepseek", "max_tokens": 64000}},
    )
    runner.state_mgr.save_answer(task_id, "keep this answer")
    ran = []
    monkeypatch.setattr(runner, "_run_loop", lambda *args, **kwargs: ran.append(args) or {"success": True})

    result = runner.resume_task(
        task_id,
        live_config={"model": {"provider": "deepseek", "max_tokens": 64000}},
        input_max_chars=0,
        input_max_tokens=64,
        output_max_tokens=12000,
    )

    assert result["success"] is False
    assert "turn-input guardrail" in result["error"]
    assert ran == []
    assert runner.state_mgr.get_task(task_id)["pending_answer"] == "keep this answer"


def test_agentic_resume_reclamps_stored_nested_models_to_live_output_budget(tmp_path, monkeypatch):
    from finagent_core.agentic.runner import AgenticRunner

    runner = AgenticRunner(api_keys={}, db_path=str(tmp_path / "tasks.db"))
    task_id = runner.state_mgr.create_task(
        "continue",
        {
            "model": {"provider": "deepseek", "max_tokens": 64000},
            "team": {"model": {"max_tokens": 48000}},
        },
    )
    captured = {}

    def fake_run_loop(task_id, query, config, *args, **kwargs):
        captured.update(config)
        return {"success": True}

    monkeypatch.setattr(runner, "_run_loop", fake_run_loop)

    result = runner.resume_task(
        task_id,
        live_config={"model": {"provider": "deepseek", "max_tokens": 64000}},
        input_max_chars=0,
        input_max_tokens=0,
        output_max_tokens=12000,
    )

    assert result["success"] is True
    assert captured["model"]["max_tokens"] == 12000
    assert captured["team"]["model"]["max_tokens"] == 12000


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


def test_nested_team_member_models_obey_turn_output_budget():
    payload = {
        "team_config": {
            "model": {"max_tokens": 64_000},
            "members": [
                {"model": {"max_tokens": 48_000}},
                {"model": {"max_tokens": 8_000}},
            ],
        },
        "agentic": {
            "reflector_model": {"max_tokens": 32_000},
            "distill_model": {"max_tokens": 24_000},
        },
    }
    assert apply_nested_output_budgets(payload, 12_000) == 5
    assert payload["team_config"]["model"]["max_tokens"] == 12_000
    assert payload["team_config"]["members"][0]["model"]["max_tokens"] == 12_000
    assert payload["team_config"]["members"][1]["model"]["max_tokens"] == 8_000
    assert payload["agentic"]["reflector_model"]["max_tokens"] == 12_000
    assert payload["agentic"]["distill_model"]["max_tokens"] == 12_000

