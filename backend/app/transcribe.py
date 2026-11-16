import base64
import os
import uuid
from dataclasses import dataclass
from typing import Protocol

import psycopg
from langchain_core.messages import HumanMessage

from app.model import ModelError
from app.turn import _CAPPED, _STOPPED, _limit
from app.usage import record_usage, tokens_for_email

PROVIDER = "google"
MODEL_ID = "gemini-3.5-flash"

MAX_AUDIO_BYTES = 10 * 1024 * 1024
AUDIO_TYPES = ("audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/mpeg")

NO_AUDIO = "No recording arrived. Record again."
TOO_LONG = "That recording is too large. Record a shorter one."
BAD_AUDIO_TYPE = "That recording format is not supported."
FAILED = "The recording could not be turned into text. Try again."

_KEY_ENV = "GEMINI_API_KEY"
_PROMPT = (
    "Transcribe the speech in this audio exactly as spoken, in the language spoken. "
    "Reply with the transcript only, with no commentary, no labels, and no quotation marks. "
    "If there is no speech, reply with nothing."
)


@dataclass(frozen=True)
class Transcript:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class Transcriber(Protocol):
    provider: str
    model_id: str

    def transcribe(self, audio: bytes, content_type: str) -> Transcript: ...


class FakeTranscriber:
    def __init__(
        self,
        text: str = "",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.provider = PROVIDER
        self.model_id = MODEL_ID
        self.heard: list[tuple[bytes, str]] = []
        self._text = text
        self._usage = (input_tokens, output_tokens, total_tokens)
        self._error = error

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        self.heard.append((audio, content_type))
        if self._error is not None:
            raise self._error
        return Transcript(self._text, *self._usage)


class GeminiTranscriber:
    def __init__(self, chat: object) -> None:
        self._chat = chat
        self.provider = PROVIDER
        self.model_id = MODEL_ID

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        message = HumanMessage(
            content=[
                {"type": "text", "text": _PROMPT},
                {
                    "type": "file",
                    "base64": base64.b64encode(audio).decode("ascii"),
                    "mime_type": content_type,
                },
            ]
        )
        try:
            answer = self._chat.invoke([message])  # type: ignore[attr-defined]
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        meta = getattr(answer, "usage_metadata", None)
        return Transcript(
            _text(getattr(answer, "content", "")).strip(),
            _count(meta, "input_tokens"),
            _count(meta, "output_tokens"),
            _count(meta, "total_tokens"),
        )


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            value = part.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


def _count(meta: object, name: str) -> int | None:
    if not isinstance(meta, dict):
        return None
    value = meta.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _key() -> str | None:
    key = os.environ.get(_KEY_ENV, "").strip()
    return key if key != "" else None


def open_transcriber() -> Transcriber:
    if _key() is None:
        return FakeTranscriber(text="This is a test transcript.")
    from langchain_google_genai import ChatGoogleGenerativeAI

    return GeminiTranscriber(
        ChatGoogleGenerativeAI(model=MODEL_ID, api_key=_key(), temperature=0)
    )


def base_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    base = content_type.split(";", 1)[0].strip().lower()
    return base if base in AUDIO_TYPES else None


@dataclass(frozen=True)
class Refused:
    status: int
    message: str


def gate(connection: psycopg.Connection, email: str) -> Refused | None:
    token_cap, stopped = _limit(connection, email.lower())
    if stopped:
        return Refused(403, _STOPPED)
    if tokens_for_email(connection, email) >= token_cap:
        return Refused(403, _CAPPED)
    return None


def record(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    email: str,
    transcriber: Transcriber,
    result: Transcript | None,
) -> None:
    # The account can be deleted while the model works. The row is kept for the email.
    present = connection.execute(
        "SELECT id FROM account WHERE id = %s",
        (account_id,),
    ).fetchone()
    record_usage(
        connection,
        account_id if present is not None else None,
        email,
        "transcription",
        transcriber.provider,
        transcriber.model_id,
        result.input_tokens if result is not None else None,
        result.output_tokens if result is not None else None,
        result.total_tokens if result is not None else None,
    )
