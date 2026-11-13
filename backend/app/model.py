import os
from dataclasses import dataclass
from typing import Literal, Protocol

MODEL_ID = "gemini-4-argon"
THINKING = "medium"
CONTEXT_TOKENS = 1048576

_KEY_ENV = "GEMINI_API_KEY"
_KEY_ENVS = (_KEY_ENV, "GOOGLE_API_KEY")


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
class Block:
    role: Literal["person", "assistant"]
    text: str

    def __post_init__(self) -> None:
        if self.role not in ("person", "assistant"):
            raise ValueError("block role is not allowed")


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class Model(Protocol):
    def complete(self, prompt: str, blocks: list[Block]) -> Reply: ...

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int: ...


class FakeModel:
    def __init__(self, reply: str = "", token_count: int = 1) -> None:
        self._reply = reply
        self._token_count = token_count

    def complete(self, prompt: str, blocks: list[Block]) -> Reply:
        return Reply(
            text=self._reply,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        return self._token_count


def open_model() -> Model:
    if _api_key() is None:
        return FakeModel()
    from app.gemini import build_gemini

    return build_gemini()
