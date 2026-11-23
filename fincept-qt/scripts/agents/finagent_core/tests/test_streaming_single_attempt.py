from unittest.mock import patch

from finagent_core import main


class _CoreAgent:
    run_calls = 0

    def __init__(self, *args, **kwargs):
        self._tracing = None
        self._guardrails = None
        self.last_warnings = []

    def run(self, *args, **kwargs):
        type(self).run_calls += 1
        return "automatic retry must not happen"

    @staticmethod
    def get_response_content(response):
        return str(response)


class _BrokenStream:
    def __init__(self, *args, **kwargs):
        pass

    def run_streaming(self, *args, **kwargs):
        yield {"type": "token", "content": "partial"}
        raise ConnectionError("stream disconnected after dispatch")


def test_midstream_failure_does_not_replay_the_paid_request():
    _CoreAgent.run_calls = 0
    with patch("finagent_core.core_agent.CoreAgent", _CoreAgent):
        with patch("finagent_core.core_agent_stream.StreamingCoreAgent", _BrokenStream):
            result = main.dispatch_action_streaming(
                "run",
                {"deepseek": "owned"},
                {"query": "Analyze AAPL"},
                {"model": {"provider": "deepseek", "model_id": "deepseek-flash"}},
            )

    assert result["success"] is False
    assert "stream disconnected" in result["error"]
    assert _CoreAgent.run_calls == 0
