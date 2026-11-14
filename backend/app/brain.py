import uuid
from collections.abc import Sequence

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.model import (
    CONTEXT_TOKENS,
    MODEL_ID,
    THINKING,
    Block,
    FakeModel,
    Model,
    ModelError,
    Reply,
    ToolCall,
    ToolSpec,
    _api_key,
)

PROVIDER = "openai"
_LANGCHAIN_PROVIDER = "openai"


_Message = SystemMessage | HumanMessage | AIMessage | ToolMessage


def open_brain() -> Model:
    if _api_key() is None:
        return FakeModel(reply="Noted.")
    chat = init_chat_model(
        f"{_LANGCHAIN_PROVIDER}:{MODEL_ID}",
        reasoning_effort=THINKING,
    )
    return LangChainBrain(chat)


class LangChainBrain:
    def __init__(self, chat: object) -> None:
        self._chat = chat
        self.provider = PROVIDER
        self.model_id = MODEL_ID
        self.context_tokens = CONTEXT_TOKENS

    def complete(
        self,
        prompt: str,
        blocks: list[Block],
        tools: Sequence[ToolSpec] = (),
    ) -> Reply:
        try:
            message = self._bound(tools).invoke(self._messages(prompt, blocks))  # type: ignore[attr-defined]
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        return _reply(message, _text(message))

    def stream(
        self,
        prompt: str,
        blocks: list[Block],
        tools: Sequence[ToolSpec] = (),
    ) -> "_ChatStream":
        try:
            chat = self._bound(tools)
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        return _ChatStream(chat, self._messages(prompt, blocks))

    def _bound(self, tools: Sequence[ToolSpec]) -> object:
        if not tools:
            return self._chat
        return self._chat.bind_tools(  # type: ignore[attr-defined]
            [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.schema,
                    },
                }
                for tool in tools
            ]
        )

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        text = "\n".join([prompt, *(block.text for block in blocks)])
        try:
            counted = self._chat.get_num_tokens(text)  # type: ignore[attr-defined]
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        if counted is None:
            raise ModelError("token count is missing")
        return int(counted)

    def _messages(
        self,
        prompt: str,
        blocks: list[Block],
    ) -> list[_Message]:
        messages: list[_Message] = [SystemMessage(content=prompt)]
        for block in blocks:
            if block.role == "person":
                messages.append(HumanMessage(content=block.text))
            elif block.role == "assistant":
                messages.append(
                    AIMessage(
                        content=block.text,
                        tool_calls=[
                            {
                                "id": call.id,
                                "name": call.name,
                                "args": call.arguments,
                                "type": "tool_call",
                            }
                            for call in block.tool_calls
                        ],
                    )
                )
            elif block.role == "tool":
                messages.append(
                    ToolMessage(content=block.text, tool_call_id=block.call_id or "")
                )
            else:
                raise ValueError("block role is not allowed")
        return messages


class _ChatStream:
    def __init__(
        self,
        chat: object,
        messages: list[_Message],
    ) -> None:
        self._chat = chat
        self._messages = messages
        self._total: object = None
        self._parts: list[str] = []
        self._usage = Reply("", None, None, None)
        self._done = False

    def __iter__(self):
        try:
            chunks = self._chat.stream(self._messages)  # type: ignore[attr-defined]
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        try:
            for chunk in chunks:
                # A tool call arrives in pieces, so the pieces are added before it is read.
                self._total = chunk if self._total is None else self._total + chunk  # type: ignore[operator]
                text = _text(chunk)
                if text != "":
                    self._parts.append(text)
                    yield text
                if getattr(chunk, "usage_metadata", None) is not None:
                    self._usage = _reply(chunk, "".join(self._parts))
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(str(error)) from None
        self._done = True

    def reply(self) -> Reply:
        text = "".join(self._parts)
        if not self._done:
            return Reply(text, None, None, None)
        return Reply(
            text,
            self._usage.input_tokens,
            self._usage.output_tokens,
            self._usage.total_tokens,
            _tool_calls(self._total),
        )


def _tool_calls(message: object) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for attribute, unreadable in (("tool_calls", False), ("invalid_tool_calls", True)):
        raw = getattr(message, attribute, None)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or name == "":
                continue
            arguments = item.get("args")
            calls.append(
                ToolCall(
                    # The tool result must name its call, so a call without an id is given one.
                    id=str(item.get("id") or f"call_{uuid.uuid4().hex}"),
                    name=name,
                    arguments=dict(arguments) if isinstance(arguments, dict) else {},
                    unreadable=unreadable,
                )
            )
    return tuple(calls)


def _text(message: object) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind in ("thinking", "reasoning"):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _reply(message: object, text: str) -> Reply:
    meta = getattr(message, "usage_metadata", None)
    calls = _tool_calls(message)
    if meta is None:
        return Reply(text, None, None, None, calls)
    return Reply(
        text,
        _count(meta, "input_tokens"),
        _count(meta, "output_tokens"),
        _count(meta, "total_tokens"),
        calls,
    )


def _count(meta: object, name: str) -> int | None:
    if isinstance(meta, dict):
        if name not in meta:
            return None
        value = meta[name]
    else:
        if not hasattr(meta, name):
            return None
        value = getattr(meta, name)
    if value is None:
        return None
    return int(value)
