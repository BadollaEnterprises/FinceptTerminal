from unittest.mock import patch

from finagent_core.core_agent import CoreAgent
from finagent_core.modules.workflow_module import _make_model
from finagent_core.registries import ModelsRegistry


def _deepseek_config():
    return {
        "provider": "deepseek",
        "model_id": "deepseek-v4-pro",
        "api_key": "owned",
        "base_url": "https://api.deepseek.com/v1",
        "reasoning_effort": "high",
    }


def test_workflow_model_preserves_explicit_reasoning_effort():
    with patch.object(ModelsRegistry, "create_model", return_value=object()) as create:
        _make_model({"deepseek": "owned"}, _deepseek_config())

    assert create.call_args.kwargs["reasoning_effort"] == "high"


def test_team_coordinator_preserves_explicit_reasoning_effort():
    agent = CoreAgent.__new__(CoreAgent)
    agent.api_keys = {"deepseek": "owned"}

    with patch.object(ModelsRegistry, "create_model", return_value=object()) as create:
        agent._create_model(_deepseek_config())

    assert create.call_args.kwargs["reasoning_effort"] == "high"
