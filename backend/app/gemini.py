from collections.abc import Iterator

from google import genai
from google.genai import types

from app.model import MODEL_ID, THINKING, Block, ModelError, Reply, _api_key

_SDK_ROLE = {"person": "user", "assistant": "model"}


def _thinking() -> types.ThinkingConfig:
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel(THINKING))


def _contents(blocks: list[Block]) -> list[types.Content]:
    contents: list[types.Content] = []
    for block in blocks:
        role = _SDK_ROLE.get(block.role)
        if role is None:
            raise ValueError("block role is not allowed")
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=block.text)],
            )
        )
    return contents


def _count_contents(prompt: str, blocks: list[Block]) -> list[types.Content]:
    # Developer API count_tokens rejects system_instruction and generation_config.
    prompt_turn = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )
    return [prompt_turn, *_contents(blocks)]


def _token(usage: object | None, name: str) -> int | None:
    if usage is None:
        return None
    value = getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _chunk_text(chunk: object) -> str:
    text = getattr(chunk, "text", None)
    if not isinstance(text, str):
        return ""
    return text


def _close_stream(stream: object) -> None:
    closer = getattr(stream, "close", None)
    if not callable(closer):
        return
    failed = False
    try:
        closer()
    except Exception:
        failed = True
    if failed:
        return


def _reply(response: object) -> Reply:
    text = getattr(response, "text", None)
    if not isinstance(text, str):
        text = ""
    usage = getattr(response, "usage_metadata", None)
    return Reply(
        text=text,
        input_tokens=_token(usage, "prompt_token_count"),
        output_tokens=_token(usage, "candidates_token_count"),
        total_tokens=_token(usage, "total_token_count"),
    )


class GeminiStream:
    def __init__(self, client: object, prompt: str, blocks: list[Block]) -> None:
        self._client = client
        self._prompt = prompt
        self._blocks = blocks
        self._parts: list[str] = []
        self._usage: object | None = None

    def _open(self) -> object:
        failed = False
        try:
            stream = self._client.models.generate_content_stream(
                model=MODEL_ID,
                contents=_contents(self._blocks),
                config=types.GenerateContentConfig(
                    system_instruction=self._prompt,
                    thinking_config=_thinking(),
                ),
            )
        except Exception:
            failed = True
        if failed:
            raise ModelError("model call failed")
        return stream

    def __iter__(self) -> Iterator[str]:
        sdk = self._open()
        try:
            while True:
                failed = False
                try:
                    chunk = next(sdk)
                except StopIteration:
                    return
                except Exception:
                    failed = True
                if failed:
                    raise ModelError("model call failed")
                usage = getattr(chunk, "usage_metadata", None)
                if usage is not None:
                    self._usage = usage
                text = _chunk_text(chunk)
                if text == "":
                    continue
                self._parts.append(text)
                yield text
        finally:
            _close_stream(sdk)

    def reply(self) -> Reply:
        usage = self._usage
        return Reply(
            text="".join(self._parts),
            input_tokens=_token(usage, "prompt_token_count"),
            output_tokens=_token(usage, "candidates_token_count"),
            total_tokens=_token(usage, "total_token_count"),
        )


class GeminiModel:
    def __init__(self, client: object) -> None:
        self._client = client

    def stream(self, prompt: str, blocks: list[Block]) -> GeminiStream:
        return GeminiStream(self._client, prompt, blocks)

    def complete(self, prompt: str, blocks: list[Block]) -> Reply:
        contents = _contents(blocks)
        failed = False
        try:
            response = self._client.models.generate_content(
                model=MODEL_ID,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    thinking_config=_thinking(),
                ),
            )
        except Exception:
            failed = True
        if failed:
            raise ModelError("model call failed")
        return _reply(response)

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        contents = _count_contents(prompt, blocks)
        failed = False
        try:
            response = self._client.models.count_tokens(
                model=MODEL_ID,
                contents=contents,
            )
        except Exception:
            failed = True
        if failed:
            raise ModelError("token count failed")
        counted = getattr(response, "total_tokens", None)
        if isinstance(counted, bool) or not isinstance(counted, int):
            raise ModelError("token count missing")
        return counted


def build_gemini() -> GeminiModel:
    key = _api_key()
    if key is None:
        raise ModelError("model key missing")
    failed = False
    try:
        client = genai.Client(api_key=key)
    except Exception:
        failed = True
    if failed:
        raise ModelError("model client failed")
    return GeminiModel(client)
