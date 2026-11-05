"""FallbackGemini: next model on failure, cool-downs, and extra rounds (no real Gemini calls)."""

import pytest
from google.adk.models import LlmRequest, LlmResponse
from google.genai import errors, types

from agents import fallback_gemini
from agents.fallback_gemini import FallbackGemini


class FakeModel:
    """Stands in for one Gemini model: answers, or fails with the given status codes in turn."""

    def __init__(self, name: str, failures: list[int]):
        self.model = name
        self.failures = list(failures)
        self.calls = 0

    async def generate_content_async(self, request, stream=False):
        self.calls += 1
        if self.failures:
            code = self.failures.pop(0)
            raise errors.APIError(code, {"error": {"code": code, "message": "fake", "status": "FAKE"}})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=f"from {self.model}")]))


def make(models: dict[str, list[int]], **settings) -> tuple[FallbackGemini, dict[str, FakeModel]]:
    llm = FallbackGemini(model_names=list(models), wait_between_rounds_seconds=0, **settings)
    fakes = {name: FakeModel(name, codes) for name, codes in models.items()}
    llm._models.update(fakes)
    return llm, fakes


async def answer(llm: FallbackGemini) -> str:
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
                         config=types.GenerateContentConfig())
    async for response in llm.generate_content_async(request):
        return response.content.parts[0].text


@pytest.fixture(autouse=True)
def no_cooldowns():
    fallback_gemini._cooling_until.clear()
    yield
    fallback_gemini._cooling_until.clear()


async def test_next_model_answers_when_the_first_fails():
    llm, fakes = make({"a": [429], "b": [503], "c": []})
    assert await answer(llm) == "from c"
    assert [f.calls for f in fakes.values()] == [1, 1, 1]


async def test_out_of_quota_model_is_skipped_on_the_next_call():
    llm, fakes = make({"a": [429, 429], "b": []})
    await answer(llm)
    await answer(llm)
    assert fakes["a"].calls == 1  # cooled down: not asked again
    assert fakes["b"].calls == 2


async def test_goes_round_again_when_every_model_failed():
    llm, fakes = make({"a": [503], "b": [503, 503]}, rounds=3, cooldown_seconds={})
    assert await answer(llm) == "from a"  # round 2: "a" recovered
    assert fakes["a"].calls == 2


async def test_last_round_tries_cooled_down_models_too():
    llm, fakes = make({"a": [429], "b": [429]}, rounds=2)
    assert await answer(llm) == "from a"  # both cooled down after round 1, tried anyway in round 2


async def test_gives_up_after_the_last_round():
    llm, _ = make({"a": [503, 503], "b": [503, 503]}, rounds=2, cooldown_seconds={})
    with pytest.raises(errors.APIError):
        await answer(llm)


async def test_other_errors_are_not_retried():
    llm, fakes = make({"a": [400], "b": []})
    with pytest.raises(errors.APIError):
        await answer(llm)
    assert fakes["b"].calls == 0
