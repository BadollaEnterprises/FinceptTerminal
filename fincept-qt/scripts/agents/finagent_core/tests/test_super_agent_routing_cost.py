from types import SimpleNamespace
from unittest.mock import patch

from finagent_core.registries import ModelsRegistry
from finagent_core.super_agent import LLMRouter, QueryIntent, SuperAgent


def _model_config():
    return {
        "provider": "deepseek",
        "model_id": "deepseek-flash",
        "api_key": "owned",
        "base_url": "https://proxy.example/v1",
    }


def test_obvious_query_routes_without_a_paid_classifier_call():
    agent = SuperAgent(api_keys={"deepseek": "owned"}, model_config=_model_config())

    with patch.object(agent.llm_router, "classify") as classify:
        routed = agent.route("Analyze AAPL and calculate its fair value")

    classify.assert_not_called()
    assert routed.intent is QueryIntent.ANALYSIS
    assert routed.confidence >= 0.3


def test_ambiguous_tied_query_can_use_the_llm_router():
    agent = SuperAgent(api_keys={"deepseek": "owned"}, model_config=_model_config())

    with patch.object(
        agent.llm_router,
        "classify",
        return_value=(QueryIntent.ECONOMICS, 0.8, "inflation dominates"),
    ) as classify:
        routed = agent.route("Explain the risk from inflation")

    classify.assert_called_once_with("Explain the risk from inflation")
    assert routed.intent is QueryIntent.ECONOMICS


def test_llm_router_is_non_thinking_and_output_bounded():
    router = LLMRouter(model_config=_model_config(), api_keys={"deepseek": "owned"})
    response = SimpleNamespace(
        content='{"intent":"analysis","confidence":0.9,"reasoning":"equity question"}'
    )

    with patch.object(ModelsRegistry, "create_model", return_value=object()) as create:
        with patch("agno.agent.Agent") as agent_class:
            agent_class.return_value.run.return_value = response
            intent, confidence, _ = router._llm_classify(
                "What do you think about AAPL?", "deepseek", "deepseek-flash"
            )

    assert intent is QueryIntent.ANALYSIS
    assert confidence == 0.9
    assert create.call_args.kwargs["max_tokens"] == 128
    assert create.call_args.kwargs["reasoning_effort"] == "none"
    assert create.call_args.kwargs["api_key"] == "owned"
    assert create.call_args.kwargs["base_url"] == "https://proxy.example/v1"
