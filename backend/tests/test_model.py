import threading

import pytest
from app.brain import LangChainBrain
from app.loop import LOOP, ask
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
from langchain_core.messages import AIMessage, AIMessageChunk


class _Chat:
    def __init__(
        self,
        message: AIMessage,
        tokens: int | None = 6,
        chunks: list[AIMessageChunk] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.message = message
        self.tokens = tokens
        self.chunks = chunks if chunks is not None else []
        self.error = error
        self.sent: list[object] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.sent.append(messages)
        if self.error is not None and not self.chunks:
            raise self.error
        return self.message

    def stream(self, messages: list[object]):
        self.sent.append(messages)
        if self.error is not None:
            raise self.error
        yield from self.chunks

    def get_num_tokens(self, text: str) -> int | None:
        if self.tokens is None:
            raise RuntimeError("token count is missing")
        return self.tokens


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

    def build(model: str, **kwargs: object) -> object:
        assert model == "google_genai:gemini-4-argon"
        assert kwargs.get("thinking_level") == "medium"
        chat = object()
        built.append(chat)
        return chat

    monkeypatch.setattr("app.brain.init_chat_model", build)
    model = open_model()
    assert built
    assert isinstance(model, LangChainBrain)
    assert model._chat is built[0]


def test_langchain_sends_the_prompt_and_blocks() -> None:
    chat = _Chat(AIMessage(content="answer"))
    model = LangChainBrain(chat)
    reply = model.complete("system prompt", _blocks())
    counted = model.count_tokens("system prompt", _blocks())
    assert reply.text == "answer"
    assert counted == 6
    sent = chat.sent[0]
    assert sent[0].content == "system prompt"
    assert [type(item).__name__ for item in sent] == ["SystemMessage", "HumanMessage", "AIMessage"]
    assert sent[1].content == "hello"
    assert sent[2].content == "hi"


def test_usage_metadata_maps_provider_fields() -> None:
    message = AIMessage(
        content=[
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": "answer"},
        ],
        usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 9},
    )
    model = LangChainBrain(_Chat(message))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=2,
        output_tokens=3,
        total_tokens=9,
    )


def test_zero_counts_stay_zero() -> None:
    message = AIMessage(
        content="answer",
        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )
    model = LangChainBrain(_Chat(message, tokens=0))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )
    assert model.count_tokens("system", _blocks()) == 0


def test_missing_usage_is_none() -> None:
    model = LangChainBrain(_Chat(AIMessage(content="answer")))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )


def test_partial_usage_leaves_absent_fields_none() -> None:
    message = type("Message", (), {"content": "answer", "usage_metadata": {"input_tokens": 8}})()
    model = LangChainBrain(_Chat(message))
    assert model.complete("system", _blocks()) == Reply(
        text="answer",
        input_tokens=8,
        output_tokens=None,
        total_tokens=None,
    )


def test_missing_token_count_is_an_error() -> None:
    model = LangChainBrain(_Chat(AIMessage(content="answer"), tokens=None))
    with pytest.raises(ModelError):
        model.count_tokens("system", _blocks())


def test_model_error_cannot_contain_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    error = ModelError("failed with test-key-value in the text")
    assert "test-key-value" not in str(error)
    assert error.__cause__ is None


def test_the_loop_calls_the_model() -> None:
    model = FakeModel(reply="from-the-graph", input_tokens=1, output_tokens=2, total_tokens=3)
    reply = ask(model, "system", _blocks())
    assert reply.text == "from-the-graph"
    assert reply.input_tokens == 1
    assert "model" in LOOP.get_graph().nodes


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


def test_langchain_stream_keeps_text_and_usage() -> None:
    chunks = [
        AIMessageChunk(content="hel"),
        AIMessageChunk(
            content=[{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "lo"}],
            usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 9},
        ),
    ]
    chat = _Chat(AIMessage(content="unused"), chunks=chunks)
    stream = LangChainBrain(chat).stream("system prompt", _blocks())
    assert list(stream) == ["hel", "lo"]
    assert stream.reply() == Reply(
        text="hello",
        input_tokens=2,
        output_tokens=3,
        total_tokens=9,
    )
    sent = chat.sent[0]
    assert sent[0].content == "system prompt"
    assert sent[1].content == "hello"
    assert sent[2].content == "hi"


def test_langchain_stream_missing_usage_is_none() -> None:
    chat = _Chat(AIMessage(content="answer"), chunks=[AIMessageChunk(content="answer")])
    stream = LangChainBrain(chat).stream("system", _blocks())
    assert list(stream) == ["answer"]
    assert stream.reply() == Reply(
        text="answer",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )


def test_langchain_stream_zero_counts_stay_zero() -> None:
    chunk = AIMessageChunk(
        content="answer",
        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )
    stream = LangChainBrain(_Chat(AIMessage(content="answer"), chunks=[chunk])).stream(
        "system",
        _blocks(),
    )
    assert list(stream) == ["answer"]
    assert stream.reply() == Reply(
        text="answer",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )


def test_langchain_stream_failure_hides_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    chat = _Chat(
        AIMessage(content="answer"),
        error=RuntimeError("authorization failed for test-key-value"),
    )
    with pytest.raises(ModelError) as caught:
        list(LangChainBrain(chat).stream("system", _blocks()))
    assert "test-key-value" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_provider_failure_hides_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")

    class _Failing:
        def invoke(self, messages: list[object]) -> AIMessage:
            raise RuntimeError("authorization failed for test-key-value")

        def get_num_tokens(self, text: str) -> int:
            return 1

    model = LangChainBrain(_Failing())
    with pytest.raises(ModelError) as caught:
        model.complete("system", _blocks())
    assert "test-key-value" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
