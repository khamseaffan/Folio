import os
import uuid

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.account import ensure_account, load_account, set_display_name
from app.db import connect, database_ready
from app.message import MessageRow, list_messages
from app.model import open_model
from app.session import end_session, open_session, start_session
from app.sign_in_proof import NOT_ALLOWED, NOT_PROVEN, SignInError, verify_allowed_sign_in
from app.thread import ThreadRow, create_thread, get_thread, list_threads
from app.turn import NO_FAILURE, start_reply, start_retry, stop_reply

app = FastAPI()

_CONTINUE = "Sign in to continue."
_NAME = "Enter a name, up to 40 characters."
_NOT_FOUND = "Not found"
_MESSAGE = "Enter a message."
_LOCAL_ORIGIN = "http://localhost:3000"


class _BrowserOrigins:
    # Checked on each request so FRONTEND_ORIGIN is current. A wildcard is never a member.

    def __contains__(self, origin: object) -> bool:
        if not isinstance(origin, str) or origin == "*":
            return False
        if origin == _LOCAL_ORIGIN:
            return True
        configured = os.environ.get("FRONTEND_ORIGIN", "").strip()
        if not configured or configured == "*":
            return False
        return origin == configured


app.add_middleware(
    CORSMiddleware,
    allow_origins=_BrowserOrigins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict[str, str]:
    if database_ready():
        return {"status": "ok"}
    response.status_code = 503
    return {"status": "down"}


def _down() -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "down"})


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _https(request: Request) -> bool:
    return request.url.scheme == "https"


def _store_session(response: Response, request: Request, session_id: uuid.UUID) -> None:
    response.set_cookie(
        key="session",
        value=str(session_id),
        path="/",
        httponly=True,
        samesite="Lax",
        secure=_https(request),
    )


def _clear_session(response: Response, request: Request) -> None:
    response.delete_cookie(
        key="session",
        path="/",
        httponly=True,
        samesite="Lax",
        secure=_https(request),
    )


def _session_id(request: Request) -> uuid.UUID | None:
    raw = request.cookies.get("session")
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _submitted_name(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    raw = body.get("display_name")
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if len(name) < 1 or len(name) > 40:
        return None
    return name


async def _id_token(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    token = body.get("id_token")
    if not isinstance(token, str) or token.strip() == "":
        return None
    return token


@app.post("/sign-in")
async def sign_in(request: Request) -> Response:
    id_token = await _id_token(request)
    if id_token is None:
        return _error(401, NOT_PROVEN)
    try:
        verified = verify_allowed_sign_in(id_token)
    except SignInError as error:
        message = str(error)
        if message == NOT_ALLOWED:
            return _error(403, NOT_ALLOWED)
        if message == "":
            return _error(401, NOT_PROVEN)
        return _error(401, message)
    try:
        account_id = ensure_account(verified.firebase_uid, verified.email)
        session_id = start_session(account_id)
        email, display_name = load_account(account_id)
    except Exception:
        return _down()
    response = JSONResponse(content={"email": email, "display_name": display_name})
    _store_session(response, request, session_id)
    return response


@app.post("/sign-out", status_code=204)
def sign_out(request: Request) -> Response:
    raw = request.cookies.get("session")
    if raw:
        session_id = _session_id(request)
        if session_id is not None:
            try:
                end_session(session_id)
            except Exception:
                return _down()
    response = Response(status_code=204)
    _clear_session(response, request)
    return response


@app.get("/session")
def get_session(request: Request) -> Response:
    session_id = _session_id(request)
    if session_id is None:
        return _error(401, _CONTINUE)
    try:
        account_id = open_session(session_id)
    except Exception:
        return _down()
    if account_id is None:
        return _error(401, _CONTINUE)
    try:
        email, display_name = load_account(account_id)
    except Exception:
        return _down()
    return JSONResponse(content={"email": email, "display_name": display_name})


@app.post("/display-name")
async def post_display_name(request: Request) -> Response:
    session_id = _session_id(request)
    if session_id is None:
        return _error(401, _CONTINUE)
    try:
        account_id = open_session(session_id)
    except Exception:
        return _down()
    if account_id is None:
        return _error(401, _CONTINUE)
    display_name = await _submitted_name(request)
    if display_name is None:
        return _error(400, _NAME)
    try:
        saved = set_display_name(account_id, session_id, display_name)
    except Exception:
        return _down()
    if saved is None:
        return _error(401, _CONTINUE)
    email, stored_name = saved
    return JSONResponse(content={"email": email, "display_name": stored_name})


def _open_account(request: Request) -> tuple[uuid.UUID, uuid.UUID] | JSONResponse:
    session_id = _session_id(request)
    if session_id is None:
        return _error(401, _CONTINUE)
    try:
        account_id = open_session(session_id)
    except Exception:
        return _down()
    if account_id is None:
        return _error(401, _CONTINUE)
    return session_id, account_id


def _parse_thread_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _message_text(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    text = body.get("text")
    if not isinstance(text, str):
        return None
    return text


def _thread_item(row: ThreadRow) -> dict[str, str]:
    return {
        "id": str(row[0]),
        "name": row[2],
        "created_at": row[4].isoformat(),
    }


def _message_item(row: MessageRow) -> dict[str, str]:
    return {
        "id": str(row[0]),
        "role": row[4],
        "kind": row[5],
        "body": row[6],
        "created_at": row[7].isoformat(),
    }


def _thread_view(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> dict[str, object] | None:
    thread = get_thread(connection, account_id, thread_id)
    if thread is None:
        return None
    messages = list_messages(connection, account_id, thread_id)
    if messages is None:
        return None
    item = _thread_item(thread)
    return {
        "id": item["id"],
        "name": item["name"],
        "created_at": item["created_at"],
        "messages": [_message_item(message) for message in messages],
    }


@app.get("/threads")
def get_threads(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = list_threads(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(content=[_thread_item(row) for row in rows])


@app.post("/threads")
def post_thread(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            created = create_thread(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(content=_thread_item(created))


@app.get("/threads/{thread_id}")
def read_thread(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(thread_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            view = _thread_view(connection, account_id, parsed)
    except Exception:
        return _down()
    if view is None:
        return _error(404, _NOT_FOUND)
    return JSONResponse(content=view)


@app.post("/threads/{thread_id}/messages")
async def post_message(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    session_id, account_id = opened
    parsed = _parse_thread_id(thread_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    text = await _message_text(request)
    if text is None:
        return _error(400, _MESSAGE)
    try:
        with connect() as connection:
            started = start_reply(
                connection,
                session_id,
                account_id,
                parsed,
                text,
                open_model(),
            )
    except Exception:
        return _down()
    if isinstance(started, str):
        return _error(403, started)
    if started is None:
        return _error(404, _NOT_FOUND)
    return StreamingResponse(
        started.events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/threads/{thread_id}/retry")
async def retry_message(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    session_id, account_id = opened
    parsed = _parse_thread_id(thread_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            started = start_retry(
                connection,
                session_id,
                account_id,
                parsed,
                open_model(),
            )
    except Exception:
        return _down()
    if started == NO_FAILURE:
        return _error(400, NO_FAILURE)
    if isinstance(started, str):
        return _error(403, started)
    if started is None:
        return _error(404, _NOT_FOUND)
    return StreamingResponse(
        started.events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/threads/{thread_id}/stop", status_code=204)
def stop_message(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(thread_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            thread = get_thread(connection, account_id, parsed)
    except Exception:
        return _down()
    if thread is None:
        return _error(404, _NOT_FOUND)
    stop_reply(account_id, parsed)
    return Response(status_code=204)
