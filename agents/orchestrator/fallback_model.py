"""A model wrapper that falls back to the next Gemini model when one is busy."""

import copy
import logging
from typing import AsyncGenerator

from google.adk.models import BaseLlm, Gemini, LlmRequest, LlmResponse
from google.genai import errors, types
from pydantic import PrivateAttr

SKIP_TO_NEXT_MODEL = {404, 429, 500, 503, 504}

# Child of ADK's "google_adk" logger, so these messages are exported as OTel logs too.
logger = logging.getLogger("google_adk.orchestrator.fallback")


class FallbackGemini(BaseLlm):
    model: str = "fallback-gemini"
    model_names: list[str]

    # One Gemini client per model, created once and reused.
    _models: dict[str, Gemini] = PrivateAttr(default_factory=dict)

    def _get_model(self, name: str) -> Gemini:
        if name not in self._models:
            self._models[name] = Gemini(
                model=name,
                retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1),
            )
        return self._models[name]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        last_error = None

        for name in self.model_names:
            gemini = self._get_model(name.strip())
            # Fresh copy of the messages + settings for each attempt.
            # (Not a full deep copy: the request also holds live tool objects,
            # such as the A2A network client, which cannot be copied.)
            request = llm_request.model_copy(
                update={
                    "model": gemini.model,
                    "contents": copy.deepcopy(llm_request.contents),
                    "config": llm_request.config.model_copy(deep=True),
                }
            )
            started = False
            try:
                async for response in gemini.generate_content_async(request, stream):
                    started = True
                    yield response
                logger.info("[fallback] answered by %s", gemini.model)
                return
            except errors.APIError as e:
                if started or e.code not in SKIP_TO_NEXT_MODEL:
                    raise
                logger.warning("[fallback] %s failed with %s, trying next model", gemini.model, e.code)
                last_error = e

        raise last_error
