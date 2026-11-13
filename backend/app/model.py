import os
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

MODEL_ID = "gpt-6.1-sol"
THINKING = "medium"
CONTEXT_TOKENS = 1_050_000

_KEY_ENV = "OPENAI_API_KEY"
_KEY_ENVS = (_KEY_ENV, "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY")


class ModelError(Exception):
    def __init__(self, message: str) -> None:
        # A provider error can repeat the request, and the request carries the key.
        super().__init__(_without_key(message))


def _without_key(message: str) -> str:
    cleaned = message
    for name in _KEY_ENVS:
        raw = os.environ.get(name)
        if raw is None:
            continue
        secret = raw.strip()
        if secret == "":
            continue
        cleaned = cleaned.replace(secret, "redacted")
        if raw != secret:
            cleaned = cleaned.replace(raw, "redacted")
    return cleaned


def _api_key() -> str | None:
    raw = os.environ.get(_KEY_ENV)
    if raw is None:
        return None
    key = raw.strip()
    if key == "":
        return None
    return key


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, object]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class Block:
    role: Literal["person", "assistant", "tool"]
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    call_id: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ("person", "assistant", "tool"):
            raise ValueError("block role is not allowed")


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    tool_calls: tuple[ToolCall, ...] = ()


class TextStream(Protocol):
    def __iter__(self) -> Iterator[str]: ...

    def reply(self) -> Reply: ...


class Model(Protocol):
    def complete(
        self,
        prompt: str,
        blocks: list[Block],
        tools: Sequence[ToolSpec] = (),
    ) -> Reply: ...

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int: ...

    def stream(
        self,
        prompt: str,
        blocks: list[Block],
        tools: Sequence[ToolSpec] = (),
    ) -> TextStream: ...


class _FakeStream:
    def __init__(
        self,
        chunks: tuple[str, ...],
        pauses: tuple[threading.Event, ...],
        usage: Reply,
    ) -> None:
        self._chunks = chunks
        self._pauses = pauses
        self._usage = usage
        self._parts: list[str] = []
        self._done = False

    def __iter__(self) -> Iterator[str]:
        for index, chunk in enumerate(self._chunks):
            if index > 0 and index - 1 < len(self._pauses):
                self._pauses[index - 1].wait()
            self._parts.append(chunk)
            yield chunk
        self._done = True

    def reply(self) -> Reply:
        if not self._done:
            return Reply(
                text="".join(self._parts),
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        return Reply(
            text="".join(self._chunks),
            input_tokens=self._usage.input_tokens,
            output_tokens=self._usage.output_tokens,
            total_tokens=self._usage.total_tokens,
            tool_calls=self._usage.tool_calls,
        )


class FakeModel:
    def __init__(
        self,
        reply: str = "",
        token_count: int = 1,
        chunks: tuple[str, ...] | None = None,
        pauses: tuple[threading.Event, ...] = (),
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        script: tuple[Reply, ...] = (),
    ) -> None:
        self._script = list(script)
        self.seen: list[tuple[list[Block], tuple[ToolSpec, ...]]] = []
        self._reply = reply
        self._token_count = token_count
        self._chunks = chunks
        self._pauses = pauses
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._total_tokens = total_tokens

    def _next(self, blocks: list[Block], tools: Sequence[ToolSpec]) -> Reply | None:
        self.seen.append((list(blocks), tuple(tools)))
        if self._script:
            return self._script.pop(0)
        return None

    def complete(
        self,
        prompt: str,
        blocks: list[Block],
        tools: Sequence[ToolSpec] = (),
    ) -> Reply:
        scripted = self._next(blocks, tools)
        if scripted is not None:
            return scripted
        return Reply(
            text=self._reply,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._total_tokens,
        )

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        return self._token_count

    def stream(
        self,
        prompt: str,
        blocks: list[Block],
        tools: Sequence[ToolSpec] = (),
    ) -> _FakeStream:
        scripted = self._next(blocks, tools)
        if scripted is not None:
            chunks = (scripted.text,) if scripted.text != "" else ()
            return _FakeStream(chunks, (), scripted)
        if self._chunks is None:
            chunks = (self._reply,) if self._reply != "" else ()
        else:
            chunks = self._chunks
        usage = Reply(
            text="",
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._total_tokens,
        )
        return _FakeStream(chunks, self._pauses, usage)


def open_model() -> Model:
    from app.brain import open_brain

    return open_brain()
