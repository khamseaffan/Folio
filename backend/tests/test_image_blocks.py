import base64
import threading

from app.brain import LangChainBrain
from app.loop import stream_turn
from app.model import Block, FakeModel, Image, Reply, ToolCall, ToolOutput, ToolSpec
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

PNG = Image("image/png", b"\x89PNG-bytes")
B64 = base64.b64encode(PNG.data).decode()
CALL = ToolCall("c1", "read_file", {"file_id": "x"})
SPEC = ToolSpec("read_file", "Read a file.", {"type": "object"})


def _drain(model: FakeModel, call_tool) -> None:
    blocks = [Block("person", "look")]
    for _ in stream_turn(model, "p", blocks, threading.Event(), (SPEC,), call_tool):
        pass


def test_images_from_a_tool_reach_the_next_model_call_on_the_tool_block() -> None:
    model = FakeModel(
        script=(
            Reply("", None, None, None, (CALL,)),
            Reply("It is red.", None, None, None),
        )
    )
    _drain(model, lambda call: ToolOutput('{"ok": true}', (PNG,)))
    second_blocks, _ = model.seen[1]
    tool_block = second_blocks[-1]
    assert (tool_block.role, tool_block.text, tool_block.images) == ("tool", '{"ok": true}', (PNG,))


def test_a_plain_string_result_still_works() -> None:
    model = FakeModel(
        script=(Reply("", None, None, None, (CALL,)), Reply("done", None, None, None))
    )
    _drain(model, lambda call: '{"ok": true}')
    assert model.seen[1][0][-1].images == ()


class _Chat:
    pass


def test_brain_puts_the_image_in_a_message_after_all_tool_results() -> None:
    brain = LangChainBrain(_Chat())
    second = ToolCall("c2", "read_file", {"file_id": "y"})
    blocks = [
        Block("person", "look"),
        Block("assistant", "", (CALL, second)),
        Block("tool", "one", call_id="c1", images=(PNG,)),
        Block("tool", "two", call_id="c2"),
    ]
    messages = brain._messages("p", blocks)
    kinds = [type(message) for message in messages[1:]]
    # Every tool result stays directly after the assistant message that called it.
    assert kinds == [HumanMessage, AIMessage, ToolMessage, ToolMessage, HumanMessage]
    image_message = messages[-1]
    assert isinstance(image_message.content, list)
    images = [part for part in image_message.content if part["type"] == "image"]
    assert len(images) == 1
    assert images[0]["mime_type"] == "image/png"
    assert images[0]["base64"] == B64
    # A tool message itself carries text only.
    assert messages[-2].content == "two"
    assert messages[-3].content == "one"


def test_the_image_message_converts_for_the_openai_provider() -> None:
    from langchain_openai.chat_models.base import _convert_message_to_dict

    brain = LangChainBrain(_Chat())
    blocks = [
        Block("assistant", "", (CALL,)),
        Block("tool", "one", call_id="c1", images=(PNG,)),
    ]
    sent = _convert_message_to_dict(brain._messages("p", blocks)[-1])
    assert sent["role"] == "user"
    urls = [part["image_url"]["url"] for part in sent["content"] if part["type"] == "image_url"]
    assert urls == [f"data:image/png;base64,{B64}"]
