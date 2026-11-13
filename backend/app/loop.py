import threading
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.model import Block, Model, Reply


class _State(TypedDict, total=False):
    prompt: str
    blocks: list[dict[str, str]]
    mode: str
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


def _blocks(raw: list[dict[str, str]]) -> list[Block]:
    return [Block(role=item["role"], text=item["text"]) for item in raw]  # type: ignore[arg-type]


def _plain(blocks: list[Block]) -> list[dict[str, str]]:
    return [{"role": block.role, "text": block.text} for block in blocks]


def _model_node(state: _State, config: RunnableConfig) -> dict[str, object]:
    configurable = config.get("configurable") or {}
    model: Model = configurable["model"]
    prompt = state["prompt"]
    blocks = _blocks(state["blocks"])
    if state.get("mode") == "complete":
        reply = model.complete(prompt, blocks)
        return {
            "text": reply.text,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "total_tokens": reply.total_tokens,
        }
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()
    stop = configurable.get("stop")
    if stop is not None and not isinstance(stop, threading.Event):
        stop = None
    stream = model.stream(prompt, blocks)
    for chunk in stream:
        if stop is not None and stop.is_set():
            break
        if isinstance(chunk, str) and chunk != "":
            writer({"text": chunk})
    reply = stream.reply()
    writer({"usage": reply})
    return {
        "text": reply.text,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "total_tokens": reply.total_tokens,
    }


def _compile() -> CompiledStateGraph:
    graph = StateGraph(_State)
    graph.add_node("model", _model_node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()


LOOP = _compile()


def ask(model: Model, prompt: str, blocks: list[Block]) -> Reply:
    result = LOOP.invoke(
        {"prompt": prompt, "blocks": _plain(blocks), "mode": "complete"},
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
):
    return LOOP.stream(
        {"prompt": prompt, "blocks": _plain(blocks), "mode": "stream"},
        config={"configurable": {"model": model, "stop": stop}},
        stream_mode="custom",
    )
