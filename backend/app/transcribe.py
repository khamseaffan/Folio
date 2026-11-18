import io
import os
import uuid
from dataclasses import dataclass
from typing import Protocol

import psycopg

from app.model import ModelError
from app.turn import _CAPPED, _STOPPED, _limit
from app.usage import record_usage, tokens_for_email

PROVIDER = "openai"
MODEL_ID = "gpt-4o-mini-transcribe"

MAX_AUDIO_BYTES = 10 * 1024 * 1024
AUDIO_TYPES = ("audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/mpeg")

NO_AUDIO = "No recording arrived. Record again."
TOO_LONG = "That recording is too large. Record a shorter one."
BAD_AUDIO_TYPE = "That recording format is not supported."
FAILED = "The recording could not be turned into text. Try again."

_KEY_ENV = "OPENAI_API_KEY"
_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}


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


class OpenAITranscriber:
    def __init__(self, client: object) -> None:
        self._client = client
        self.provider = PROVIDER
        self.model_id = MODEL_ID

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        name = f"recording.{_EXTENSIONS.get(content_type, 'webm')}"
        file = io.BytesIO(audio)
        file.name = name
        try:
            answer = self._client.audio.transcriptions.create(  # type: ignore[attr-defined]
                model=MODEL_ID, file=file, response_format="json"
            )
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        text = getattr(answer, "text", "")
        meta = getattr(answer, "usage", None)
        return Transcript(
            text.strip() if isinstance(text, str) else "",
            _count(meta, "input_tokens"),
            _count(meta, "output_tokens"),
            _count(meta, "total_tokens"),
        )


def _count(meta: object, name: str) -> int | None:
    value = meta.get(name) if isinstance(meta, dict) else getattr(meta, name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _key() -> str | None:
    key = os.environ.get(_KEY_ENV, "").strip()
    return key if key != "" else None


def open_transcriber() -> Transcriber:
    if _key() is None:
        return FakeTranscriber(text="This is a test transcript.")
    from openai import OpenAI

    return OpenAITranscriber(OpenAI(api_key=_key()))


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
