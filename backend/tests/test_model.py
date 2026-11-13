import threading
from types import SimpleNamespace

import pytest
from app.gemini import GeminiModel
from app.model import (
    CONTEXT_TOKENS,
    MODEL_ID,
    THINKING,
    Block,
    FakeModel,
    ModelError,
    Reply,
    open_model,
)


def _level(config: object) -> object:
    thinking = getattr(config, "thinking_config", None)
    if thinking is None:
        generation = getattr(config, "generation_config", None)
        thinking = getattr(generation, "thinking_config", None)
    assert thinking is not None
    return thinking.thinking_level


class _Models:
    def __init__(
        self,
        response: object,
        counted: object,
        stream_chunks: list[object] | None = None,
        stream_error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.counted = counted
        self.stream_chunks = stream_chunks if stream_chunks is not None else []
        self.stream_error = stream_error
        self.generate_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.count_calls: list[dict[str, object]] = []

    def generate_content(self, *, model: str, contents: object, config: object) -> object:
        self.generate_calls.append(
            {"model": model, "contents": contents, "config": config}
        )
        return self.response

    def generate_content_stream(self, *, model: str, contents: object, config: object) -> object:
        self.stream_calls.append(
            {"model": model, "contents": contents, "config": config}
        )
        if self.stream_error is not None:
            raise self.stream_error
        chunks = self.stream_chunks

        def _walk():
            yield from chunks

        return _walk()

    def count_tokens(self, *, model: str, contents: object) -> object:
        self.count_calls.append({"model": model, "contents": contents})
        return self.counted


def _gemini(
    response: object,
    counted: object,
    stream_chunks: list[object] | None = None,
    stream_error: BaseException | None = None,
) -> tuple[GeminiModel, _Models]:
    models = _Models(response, counted, stream_chunks, stream_error)
    return GeminiModel(SimpleNamespace(models=models)), models


def _blocks() -> list[Block]:
    return [
        Block(role="person", text="hello"),
        Block(role="assistant", text="hi"),
    ]


def test_constants() -> None:
    assert MODEL_ID == "gemini-4-argon"
    assert THINKING == "medium"
    assert CONTEXT_TOKENS == 1048576


def test_fake_returns_configured_reply_and_scripted_count() -> None:
    model = FakeModel(reply="hello", token_count=4)
    blocks = _blocks()
    assert model.complete("system", blocks) == Reply(
        text="hello",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )
    assert model.count_tokens("system", blocks) == 4
    assert FakeModel().count_tokens("system", []) == 1
    counted = FakeModel(reply="hello", input_tokens=2, output_tokens=3, total_tokens=5)
    assert counted.complete("system", blocks) == Reply(
        text="hello",
        input_tokens=2,
        output_tokens=3,
        total_tokens=5,
    )
    zeros = FakeModel(reply="z", input_tokens=0, output_tokens=0, total_tokens=0)
    assert zeros.complete("system", []) == Reply(
        text="z",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )


def test_block_role_must_be_person_or_assistant() -> None:
    with pytest.raises(ValueError):
        Block(role="user", text="no")  # type: ignore[arg-type]


def test_open_model_without_a_key_returns_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "other-key")
    missing = open_model()
    assert isinstance(missing, FakeModel)
    noted = missing.complete("prompt", [])
    assert noted.text == "Noted."
    assert noted.input_tokens is None
    assert noted.output_tokens is None
    assert noted.total_tokens is None
    assert list(missing.stream("prompt", [])) == ["Noted."]
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    assert isinstance(open_model(), FakeModel)


def test_open_model_with_a_key_does_not_call_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    built: list[object] = []

    def client(*, api_key: str) -> object:
        if api_key.strip() == "":
            raise AssertionError("blank key")
        built.append(object())
        return built[-1]

    monkeypatch.setattr("app.gemini.genai.Client", client)
    model = open_model()
    assert built
    assert isinstance(model, GeminiModel)
    assert getattr(model, "_client", None) is built[0]


def test_gemini_sends_the_model_and_medium_thinking() -> None:
    response = SimpleNamespace(text="answer", usage_metadata=None)
    model, models = _gemini(response, SimpleNamespace(total_tokens=6))
    blocks = _blocks()
    reply = model.complete("system prompt", blocks)
    counted = model.count_tokens("system prompt", blocks)

    assert reply.text == "answer"
    assert counted == 6
    assert models.generate_calls[0]["model"] == "gemini-4-argon"
    assert models.count_calls[0]["model"] == "gemini-4-argon"
    generate_config = models.generate_calls[0]["config"]
    assert _level(generate_config) == "MEDIUM"
    assert getattr(generate_config, "system_instruction") == "system prompt"
    generate_contents = models.generate_calls[0]["contents"]
    assert isinstance(generate_contents, list)
    assert [item.role for item in generate_contents] == ["user", "model"]
    assert generate_contents[0].parts[0].text == "hello"
    assert generate_contents[1].parts[0].text == "hi"
    count_contents = models.count_calls[0]["contents"]
    assert isinstance(count_contents, list)
    assert [item.role for item in count_contents] == ["user", "user", "model"]
    assert [item.parts[0].text for item in count_contents] == [
        "system prompt",
        "hello",
        "hi",
    ]
    assert "config" not in models.count_calls[0]


def test_usage_metadata_maps_provider_fields() -> None:
    usage = SimpleNamespace(
        prompt_token_count=2,
        candidates_token_count=3,
        total_token_count=9,
    )
    response = SimpleNamespace(text="answer", thoughts="hidden", usage_metadata=usage)
    model, _models = _gemini(response, SimpleNamespace(total_tokens=1))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=2,
        output_tokens=3,
        total_tokens=9,
    )


def test_zero_counts_stay_zero() -> None:
    usage = SimpleNamespace(
        prompt_token_count=0,
        candidates_token_count=0,
        total_token_count=0,
    )
    response = SimpleNamespace(text="answer", usage_metadata=usage)
    model, _models = _gemini(response, SimpleNamespace(total_tokens=0))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )
    assert model.count_tokens("system", _blocks()) == 0


def test_missing_usage_is_none() -> None:
    response = SimpleNamespace(text="answer", usage_metadata=None)
    model, _models = _gemini(response, SimpleNamespace(total_tokens=1))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )


def test_partial_usage_leaves_absent_fields_none() -> None:
    usage = SimpleNamespace(prompt_token_count=8, candidates_token_count=None)
    response = SimpleNamespace(text="answer", usage_metadata=usage)
    model, _models = _gemini(response, SimpleNamespace(total_tokens=1))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=8,
        output_tokens=None,
        total_tokens=None,
    )


def test_missing_token_count_is_an_error() -> None:
    model, _models = _gemini(
        SimpleNamespace(text="answer", usage_metadata=None),
        SimpleNamespace(total_tokens=None),
    )
    with pytest.raises(ModelError) as caught:
        model.count_tokens("system", _blocks())
    assert "test-key-value" not in str(caught.value)


def test_model_error_cannot_contain_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    error = ModelError("failed with test-key-value in the text")
    assert "test-key-value" not in str(error)
    assert error.__cause__ is None


def test_fake_stream_waits_between_chunks_and_leaves_missing_usage_none() -> None:
    gate = threading.Event()
    model = FakeModel(chunks=("left", "right"), pauses=(gate,))
    stream = model.stream("system", _blocks())
    found = iter(stream)
    assert next(found) == "left"
    holder: list[str] = []

    def pull() -> None:
        holder.append(next(found))

    worker = threading.Thread(target=pull)
    worker.start()
    worker.join(0.1)
    assert worker.is_alive()
    gate.set()
    worker.join(1)
    assert holder == ["right"]
    with pytest.raises(StopIteration):
        next(found)
    assert stream.reply() == Reply(
        text="leftright",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )


def test_fake_stream_closed_early_does_not_invent_zero_usage() -> None:
    gate = threading.Event()
    model = FakeModel(
        chunks=("left", "right"),
        pauses=(gate,),
        input_tokens=4,
        output_tokens=5,
        total_tokens=9,
    )
    stream = model.stream("system", _blocks())
    found = iter(stream)
    assert next(found) == "left"
    found.close()
    reply = stream.reply()
    assert reply.text == "left"
    assert reply.input_tokens is None
    assert reply.output_tokens is None
    assert reply.total_tokens is None
    gate.set()


def test_fake_stream_keeps_a_finished_zero_count() -> None:
    model = FakeModel(chunks=("hi",), input_tokens=0, output_tokens=0, total_tokens=0)
    stream = model.stream("system", _blocks())
    assert list(stream) == ["hi"]
    assert stream.reply() == Reply(
        text="hi",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )


def test_gemini_stream_uses_the_same_model_and_medium_thinking() -> None:
    usage = SimpleNamespace(
        prompt_token_count=2,
        candidates_token_count=3,
        total_token_count=9,
    )
    chunks = [
        SimpleNamespace(text="hel", usage_metadata=None),
        SimpleNamespace(text="lo", thoughts="hidden", usage_metadata=usage),
    ]
    model, models = _gemini(SimpleNamespace(text="unused"), SimpleNamespace(total_tokens=1), chunks)
    blocks = _blocks()
    stream = model.stream("system prompt", blocks)
    assert list(stream) == ["hel", "lo"]
    assert stream.reply() == Reply(
        text="hello",
        input_tokens=2,
        output_tokens=3,
        total_tokens=9,
    )
    assert models.stream_calls[0]["model"] == "gemini-4-argon"
    stream_config = models.stream_calls[0]["config"]
    assert _level(stream_config) == "MEDIUM"
    assert getattr(stream_config, "system_instruction") == "system prompt"
    stream_contents = models.stream_calls[0]["contents"]
    assert isinstance(stream_contents, list)
    assert [item.role for item in stream_contents] == ["user", "model"]
    assert stream_contents[0].parts[0].text == "hello"
    assert stream_contents[1].parts[0].text == "hi"


def test_gemini_stream_missing_usage_is_none() -> None:
    chunks = [SimpleNamespace(text="answer", usage_metadata=None)]
    model, _models = _gemini(
        SimpleNamespace(text="answer"),
        SimpleNamespace(total_tokens=1),
        chunks,
    )
    stream = model.stream("system", _blocks())
    assert list(stream) == ["answer"]
    assert stream.reply() == Reply(
        text="answer",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )


def test_gemini_stream_zero_counts_stay_zero() -> None:
    usage = SimpleNamespace(
        prompt_token_count=0,
        candidates_token_count=0,
        total_token_count=0,
    )
    chunks = [SimpleNamespace(text="answer", usage_metadata=usage)]
    model, _models = _gemini(
        SimpleNamespace(text="answer"),
        SimpleNamespace(total_tokens=0),
        chunks,
    )
    stream = model.stream("system", _blocks())
    assert list(stream) == ["answer"]
    assert stream.reply() == Reply(
        text="answer",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )


def test_gemini_stream_failure_hides_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    model, _models = _gemini(
        SimpleNamespace(text="answer"),
        SimpleNamespace(total_tokens=1),
        stream_error=RuntimeError("authorization failed for test-key-value"),
    )
    with pytest.raises(ModelError) as caught:
        list(model.stream("system", _blocks()))
    assert "test-key-value" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_provider_failure_hides_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")

    class _Failing:
        def generate_content(self, *, model: str, contents: object, config: object) -> object:
            raise RuntimeError("authorization failed for test-key-value")

    model = GeminiModel(SimpleNamespace(models=_Failing()))
    with pytest.raises(ModelError) as caught:
        model.complete("system", _blocks())
    assert "test-key-value" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
