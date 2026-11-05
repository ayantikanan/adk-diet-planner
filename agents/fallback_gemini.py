"""LLM with fallbacks: tries a list of models, in order, until one answers.

Used by every agent (orchestrator, grocery_agent, workout_agent); each lists its models and the
fallback settings in its config.yaml. Two kinds of model names:

- "gemini-3.5-flash"                   Gemini, with GOOGLE_API_KEY
- "<provider>/<model>", for example "groq/llama-3.3-70b-versatile"
                                       another provider through LiteLLM, with that provider's
                                       key (PROVIDER_KEYS below). Models of a provider whose key
                                       isn't set are left out, so adding a key turns them on.

- A busy (503), rate-limited or out-of-quota (429), missing (404) or failing (500/504) model
  is skipped and the next one is tried straight away. So is one whose key is refused (401/403),
  and, for the other providers, one that can't handle the request (400, 413, 422: for example
  a context window too small for the conversation).
- A model that failed is "cooled down" for a while (cooldown_seconds, per status code), so the
  next calls don't lose seconds on it: a model whose daily quota is used up keeps answering 429.
- When every model failed, it waits and goes round the list again (rounds); the last round tries
  every model, cooled down or not.

This file sits in agents/ but isn't an agent: ADK lists only the agent folders.
"""

import asyncio
import copy
import logging
import os
import time
from typing import AsyncGenerator

from google.adk.models import BaseLlm, Gemini, LlmRequest, LlmResponse
from google.genai import errors, types
from pydantic import PrivateAttr

SKIP_TO_NEXT_MODEL = {401, 403, 404, 429, 500, 502, 503, 504}
# Other providers also reject requests their model can't take (tool format, context size):
# another model may well take them.
SKIP_TO_NEXT_OTHER_PROVIDER = SKIP_TO_NEXT_MODEL | {400, 413, 422}

# "<provider>/<model>" names: the environment variable holding each provider's API key.
PROVIDER_KEYS = {
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Child of ADK's "google_adk" logger, so these messages are exported as OTel logs too.
logger = logging.getLogger("google_adk.fallback")

# Shared by every agent in this process: model name -> time.monotonic() until which it's skipped.
_cooling_until: dict[str, float] = {}


def is_other_provider(name: str) -> bool:
    return "/" in name


def has_key(name: str) -> bool:
    """Gemini names need nothing more; "<provider>/<model>" needs that provider's key, if we know it."""
    if not is_other_provider(name):
        return True
    env = PROVIDER_KEYS.get(name.split("/", 1)[0])
    return env is None or bool(os.getenv(env))


def status_code(error: Exception) -> int | None:
    """The HTTP status of a failed call: Gemini's APIError, or LiteLLM's exceptions."""
    if isinstance(error, errors.APIError):
        return error.code
    code = getattr(error, "status_code", None)
    if isinstance(code, int):
        return code
    if type(error).__name__ in ("Timeout", "APIConnectionError", "ServiceUnavailableError"):
        return 503
    return None


class FallbackGemini(BaseLlm):
    model: str = "fallback-gemini"
    model_names: list[str]
    rounds: int = 3
    wait_between_rounds_seconds: float = 5
    cooldown_seconds: dict[int, float] = {429: 300, 404: 3600, 503: 60, 401: 3600, 403: 3600}
    request_timeout_seconds: float = 120  # other providers only

    # One client per model, created once and reused.
    _models: dict[str, BaseLlm] = PrivateAttr(default_factory=dict)

    @classmethod
    def from_config(cls, models: list[str], fallback: dict | None = None) -> "FallbackGemini":
        """Builds it from an agent's config.yaml: `models` plus the optional `fallback` section."""
        llm = cls(model_names=models, **(fallback or {}))
        skipped = [n for n in llm.model_names if not has_key(n.strip())]
        if skipped:
            logger.info("[fallback] no API key set for: %s (left out)", ", ".join(skipped))
        return llm

    def _get_model(self, name: str) -> BaseLlm:
        if name not in self._models:
            if is_other_provider(name):
                from google.adk.models.lite_llm import LiteLlm

                # LiteLLM reads the provider's key from the environment (PROVIDER_KEYS).
                self._models[name] = LiteLlm(model=name, num_retries=0, timeout=self.request_timeout_seconds)
            else:
                # No retries inside one model: moving on to the next model is faster than waiting.
                self._models[name] = Gemini(model=name, retry_options=types.HttpRetryOptions(attempts=1))
        return self._models[name]

    def _order(self, final_round: bool) -> list[str]:
        names = [n.strip() for n in self.model_names if has_key(n.strip())]
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
                except Exception as e:  # noqa: BLE001 - only model errors are skipped, see below
                    code = status_code(e)
                    skippable = SKIP_TO_NEXT_OTHER_PROVIDER if is_other_provider(name) else SKIP_TO_NEXT_MODEL
                    if started or code not in skippable:
                        raise
                    if cooldown := self.cooldown_seconds.get(code):
                        _cooling_until[name] = time.monotonic() + cooldown
                    logger.warning("[fallback] %s failed with %s, trying next model", name, code)
                    last_error = e

            if round_no < self.rounds:
                wait = self.wait_between_rounds_seconds * round_no
                logger.warning("[fallback] every model failed (round %d of %d), trying again in %ss",
                               round_no, self.rounds, wait)
                await asyncio.sleep(wait)

        if last_error is None:
            raise RuntimeError("No model to try: check the models list and the API keys.")
        raise last_error
