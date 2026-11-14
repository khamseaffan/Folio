import threading
from collections.abc import Callable, Sequence
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.model import Block, Model, Reply, ToolCall, ToolSpec

MAX_TOOL_ROUNDS = 8
ROUNDS_REACHED = "I stopped after 8 tool steps."


class _State(TypedDict, total=False):
    prompt: str
    blocks: list[Block]
    mode: str
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    calls: list[ToolCall]
    rounds: int
    halted: bool


def _stop(config: RunnableConfig) -> threading.Event | None:
    stop = (config.get("configurable") or {}).get("stop")
    if isinstance(stop, threading.Event):
        return stop
    return None


def _counted(model: Model, prompt: str, blocks: list[Block], reply: Reply) -> Reply:
    # A stopped call ends before the provider sends its counts. It was still paid for, so the
    # text that went out and the text that came back are counted here and marked as an estimate.
    if None not in (reply.input_tokens, reply.output_tokens, reply.total_tokens):
        return reply
    try:
        sent = model.count_tokens(prompt, blocks)
        received = 0
        if reply.text != "":
            received = model.count_tokens("", [Block(role="assistant", text=reply.text)])
    except Exception:
        return reply
    return Reply(reply.text, sent, received, sent + received, estimated=True)


def _model_node(state: _State, config: RunnableConfig) -> dict[str, object]:
    configurable = config.get("configurable") or {}
    model: Model = configurable["model"]
    prompt = state["prompt"]
    blocks = state["blocks"]
    if state.get("mode") == "complete":
        reply = model.complete(prompt, blocks)
        return {
            "text": reply.text,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "total_tokens": reply.total_tokens,
            "calls": [],
        }
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()
    stop = _stop(config)
    tools: Sequence[ToolSpec] = configurable.get("tools") or ()
    stream = model.stream(prompt, blocks, tools) if tools else model.stream(prompt, blocks)
    said = state.get("text", "")
    # Words from before a tool round and words after it are separate paragraphs.
    gap = "\n\n" if said != "" else ""
    try:
        for chunk in stream:
            if stop is not None and stop.is_set():
                break
            if isinstance(chunk, str) and chunk != "":
                if gap != "":
                    said += gap
                    writer({"text": gap})
                    gap = ""
                said += chunk
                writer({"text": chunk})
    except Exception:
        # The call was sent, so it still gets a usage row, with its counts missing.
        writer({"usage": Reply(stream.reply().text, None, None, None)})
        raise
    reply = stream.reply()
    stopped = stop is not None and stop.is_set()
    if stopped:
        reply = _counted(model, prompt, blocks, reply)
    writer({"usage": reply})
    calls = list(reply.tool_calls)
    if stopped:
        calls = []
    if calls and state.get("rounds", 0) >= MAX_TOOL_ROUNDS:
        calls = []
        notice = ROUNDS_REACHED if said == "" else "\n\n" + ROUNDS_REACHED
        said += notice
        writer({"text": notice})
    update: dict[str, object] = {"text": said, "calls": calls}
    if calls:
        update["blocks"] = [
            *blocks,
            Block(role="assistant", text=reply.text, tool_calls=tuple(calls)),
        ]
    return update


def _tools_node(state: _State, config: RunnableConfig) -> dict[str, object]:
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()
    configurable = config.get("configurable") or {}
    call_tool: Callable[[ToolCall], str] = configurable["call_tool"]
    stop = _stop(config)
    blocks = list(state["blocks"])
    halted = False
    for call in state.get("calls", []):
        if stop is not None and stop.is_set():
            halted = True
            break
        writer({"tool": call.name})
        blocks.append(Block(role="tool", text=call_tool(call), call_id=call.id))
    if stop is not None and stop.is_set():
        halted = True
    return {
        "blocks": blocks,
        "calls": [],
        "rounds": state.get("rounds", 0) + 1,
        "halted": halted,
    }


def _after_model(state: _State) -> str:
    return "tools" if state.get("calls") else END


def _after_tools(state: _State) -> str:
    return END if state.get("halted") else "model"


def _compile() -> CompiledStateGraph:
    graph = StateGraph(_State)
    graph.add_node("model", _model_node)
    graph.add_node("tools", _tools_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _after_model, ["tools", END])
    graph.add_conditional_edges("tools", _after_tools, ["model", END])
    return graph.compile()


LOOP = _compile()


def ask(model: Model, prompt: str, blocks: list[Block]) -> Reply:
    result = LOOP.invoke(
        {"prompt": prompt, "blocks": list(blocks), "mode": "complete"},
        config={"configurable": {"model": model}},
    )
    return Reply(
        text=str(result.get("text", "")),
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        total_tokens=result.get("total_tokens"),
    )


def stream_turn(
    model: Model,
    prompt: str,
    blocks: list[Block],
    stop: threading.Event,
    tools: Sequence[ToolSpec] = (),
    call_tool: Callable[[ToolCall], str] | None = None,
):
    configurable: dict[str, object] = {"model": model, "stop": stop}
    if call_tool is not None and tools:
        configurable["tools"] = tuple(tools)
        configurable["call_tool"] = call_tool
    return LOOP.stream(
        {"prompt": prompt, "blocks": list(blocks), "mode": "stream"},
        config={"configurable": configurable},
        stream_mode="custom",
    )
