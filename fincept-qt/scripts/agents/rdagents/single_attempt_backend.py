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
    """Prevent hidden continuation calls and plaintext settings logging."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # RD-Agent's constructor logs LITELLM_SETTINGS.model_dump(), including
        # openai_api_key. Mark the project-owned subclass as already logged so
        # the parent preserves its remaining initialization without writing
        # the user's DeepSeek credential to the RD-Agent log.
        self.__class__._has_logged_settings = True
        super().__init__(*args, **kwargs)

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

