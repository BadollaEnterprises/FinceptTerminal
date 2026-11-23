from unittest.mock import patch

from finagent_core import main
from finagent_core.core_agent_stream import StreamingCoreAgent


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
        raise ConnectionError(
            "stream disconnected after dispatch api_key=sk-stream-secret-value"
        )


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
    assert "sk-stream-secret-value" not in result["error"]
    assert "[REDACTED]" in result["error"]
    assert _CoreAgent.run_calls == 0


def test_native_stream_exception_redacts_provider_credentials():
    class BrokenCore:
        _guardrails = None
        build_warnings = []

        def _create_agent(self, config):
            raise RuntimeError("Authorization: Bearer provider-secret-token")

    emitted = []
    stream = StreamingCoreAgent(
        stream_callback=lambda kind, content, metadata=None: emitted.append(
            (kind, content)
        )
    )
    chunks = list(stream._stream_native(BrokenCore(), "query", {}, None))

    assert chunks[-1]["type"] == "error"
    assert "provider-secret-token" not in chunks[-1]["content"]
    assert "[REDACTED]" in chunks[-1]["content"]
    assert "provider-secret-token" not in str(emitted)
