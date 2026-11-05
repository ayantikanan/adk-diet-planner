"""Gemini with fallbacks: tries a list of models, in order, until one answers.

Used by every agent (orchestrator, grocery_agent, workout_agent); each lists its models and the
fallback settings in its config.yaml:

- A busy (503), rate-limited or out-of-quota (429), missing (404) or failing (500/504) model
  is skipped and the next one is tried straight away.
- A model that failed is "cooled down" for a while (cooldown_seconds, per status code), so the
  next calls don't lose seconds on it: a model whose daily quota is used up keeps answering 429.
- When every model failed, it waits and goes round the list again (rounds); the last round tries
  every model, cooled down or not.

This file sits in agents/ but isn't an agent: ADK lists only the agent folders.
"""

import asyncio
import copy
import logging
import time
from typing import AsyncGenerator

from google.adk.models import BaseLlm, Gemini, LlmRequest, LlmResponse
from google.genai import errors, types
from pydantic import PrivateAttr

SKIP_TO_NEXT_MODEL = {404, 429, 500, 503, 504}

# Child of ADK's "google_adk" logger, so these messages are exported as OTel logs too.
logger = logging.getLogger("google_adk.fallback")

# Shared by every agent in this process: model name -> time.monotonic() until which it's skipped.
_cooling_until: dict[str, float] = {}


class FallbackGemini(BaseLlm):
    model: str = "fallback-gemini"
    model_names: list[str]
    rounds: int = 3
    wait_between_rounds_seconds: float = 5
    cooldown_seconds: dict[int, float] = {429: 300, 404: 3600, 503: 60}

    # One Gemini client per model, created once and reused.
    _models: dict[str, Gemini] = PrivateAttr(default_factory=dict)

    @classmethod
    def from_config(cls, models: list[str], fallback: dict | None = None) -> "FallbackGemini":
        """Builds it from an agent's config.yaml: `models` plus the optional `fallback` section."""
        return cls(model_names=models, **(fallback or {}))

    def _get_model(self, name: str) -> Gemini:
        if name not in self._models:
            # No retries inside one model: moving on to the next model is faster than waiting.
            self._models[name] = Gemini(model=name, retry_options=types.HttpRetryOptions(attempts=1))
        return self._models[name]

    def _order(self, final_round: bool) -> list[str]:
        names = [n.strip() for n in self.model_names]
        if final_round:
            return names  # last chance: try every model
        now = time.monotonic()
        ready = [n for n in names if _cooling_until.get(n, 0) <= now]
        return ready or names

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        last_error = None

        for round_no in range(1, self.rounds + 1):
            for name in self._order(final_round=round_no == self.rounds):
                gemini = self._get_model(name)
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
                    _cooling_until.pop(name, None)
                    logger.info("[fallback] answered by %s", gemini.model)
                    return
                except errors.APIError as e:
                    if started or e.code not in SKIP_TO_NEXT_MODEL:
                        raise
                    if cooldown := self.cooldown_seconds.get(e.code):
                        _cooling_until[name] = time.monotonic() + cooldown
                    logger.warning("[fallback] %s failed with %s, trying next model", gemini.model, e.code)
                    last_error = e

            if round_no < self.rounds:
                wait = self.wait_between_rounds_seconds * round_no
                logger.warning("[fallback] every model failed (round %d of %d), trying again in %ss",
                               round_no, self.rounds, wait)
                await asyncio.sleep(wait)

        raise last_error
