import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator


class LiveReply:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._listening = True
        self._lock = threading.Lock()

    def publish(self, payload: dict[str, object]) -> None:
        self._put("data: " + json.dumps(payload) + "\n\n")

    def finish(self) -> None:
        self._put(None)

    def _put(self, item: str | None) -> None:
        with self._lock:
            if not self._listening:
                return
            loop = self._loop
            queue = self._queue
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            return

    def _detach(self) -> None:
        # Closing the response is not a stop. end_session does not call this.
        with self._lock:
            self._listening = False

    async def events(self) -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(self._queue.get(), 0.25)
                except TimeoutError:
                    continue
                if item is None:
                    return
                yield item
        finally:
            self._detach()


_live_lock = threading.Lock()
_live: dict[tuple[uuid.UUID, uuid.UUID], list[LiveReply]] = {}


def register(account_id: uuid.UUID, thread_id: uuid.UUID, live: LiveReply) -> None:
    with _live_lock:
        _live.setdefault((account_id, thread_id), []).append(live)


def unregister(account_id: uuid.UUID, thread_id: uuid.UUID, live: LiveReply) -> None:
    with _live_lock:
        rows = _live.get((account_id, thread_id))
        if rows is None:
            return
        if live in rows:
            rows.remove(live)
        if not rows:
            del _live[(account_id, thread_id)]


def stop_reply(account_id: uuid.UUID, thread_id: uuid.UUID) -> None:
    with _live_lock:
        rows = list(_live.get((account_id, thread_id), ()))
    for live in rows:
        live.stop.set()


def stop_account(account_id: uuid.UUID) -> None:
    with _live_lock:
        rows = [live for key, replies in _live.items() if key[0] == account_id for live in replies]
    for live in rows:
        live.stop.set()
