"""Cost-bounded RD-Agent backend used by Fincept Terminal.

RD-Agent's base backend automatically asks the provider to continue as many as
five more times when a response reaches ``max_tokens``.  That makes the profile
limit an output chunk size rather than a per-step spending ceiling.  Fincept
keeps the first response and changes only its finish marker, so the upstream
auto-continuation loop stops after one provider request.
"""

from __future__ import annotations

from typing import Any

from rdagent.oai.backend import LiteLLMAPIBackend


class SingleAttemptLiteLLMBackend(LiteLLMAPIBackend):
    """Prevent an RD-Agent step from silently issuing continuation calls."""

    def _create_chat_completion_inner_function(
        self, *args: Any, **kwargs: Any
    ) -> tuple[str, str | None]:
        content, finish_reason = super()._create_chat_completion_inner_function(
            *args, **kwargs
        )
        if finish_reason == "length":
            # Any value other than the literal ``length`` stops RD-Agent's
            # six-call continuation loop. The content is intentionally left
            # unchanged: structured-output validation will fail visibly if
            # the provider returned incomplete JSON.
            finish_reason = "fincept_length_limit"
        return content, finish_reason

