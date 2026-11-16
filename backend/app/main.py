import logging
import os
import secrets
import uuid
from urllib.parse import urlsplit

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import (
    ability,
    knowledge_file,
    operations,
    report,
    report_source,
    run,
    schedule,
    transcribe,
)
from app.account import (
    delete_account,
    ensure_account,
    load_account,
    load_firebase_uid,
    set_display_name,
)
from app.db import connect, database_ready
from app.file import (
    ACCOUNT_LIMIT_BYTES,
    MAX_UPLOAD_BYTES,
    TOO_BIG,
    FileRefused,
    add_drive_copy,
    begin_upload,
    byte_count,
    complete_upload,
    delete_account_bytes,
    get_file,
    list_files,
)
from app.google_drive import (
    CANNOT_OPEN,
    DRIVE_DOWN,
    NOT_CONNECTED,
    DriveError,
    DriveMissing,
    DriveReconnect,
    DriveTooBig,
    GoogleDrive,
    open_drive,
    plan_copy,
    valid_file_id,
)
from app.integration import (
    GOOGLE_DRIVE,
    load_integration,
    remove_integration,
    save_integration,
)
from app.kms import open_kms
from app.live import stop_account, stop_reply
from app.message import MessageRow, list_messages
from app.model import open_model
from app.run_activity import list_activity
from app.session import (
    end_account_session,
    end_session,
    list_open_sessions,
    open_session,
    session_handle,
    start_session,
)
from app.sign_in_proof import (
    NOT_ALLOWED,
    NOT_PROVEN,
    SignInError,
    delete_firebase_user,
    verify_allowed_sign_in,
)
from app.storage import open_storage
from app.thread import ThreadRow, get_thread, list_threads, list_threads_with_latest
from app.transcribe import open_transcriber
from app.turn import NO_FAILURE, start_reply, start_retry
from app.usage import token_totals_for_email

app = FastAPI()

_CONTINUE = "Sign in to continue."
_NAME = "Enter a name, up to 40 characters."
_NOT_FOUND = "Not found"
_MESSAGE = "Enter a message."
_NOT_DELETED = "The account could not be deleted. Try again."
_FOREIGN = "This request did not come from Folio."
_LOCAL_ORIGIN = "http://localhost:3000"
_log = logging.getLogger("folio.api")


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
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _from_folio(origin: str, headers: dict[str, str]) -> bool:
    if origin in _BrowserOrigins():
        return True
    host = urlsplit(origin).netloc.lower()
    if host == "":
        return False
    # The site forwards /api to this service, so the browser's own host arrives in a header.
    forwarded = headers.get("x-forwarded-host", "").split(",")[0].strip().lower()
    return host in (forwarded, headers.get("host", "").strip().lower())


class _OwnOrigin:
    # A browser names the page a change comes from. A change from another site is refused
    # before any route runs, so the session cookie cannot be used by a page that is not Folio.

    def __init__(self, inner: ASGIApp) -> None:
        self._inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _CHANGING:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope["headers"]
            }
            origin = headers.get("origin")
            if origin is not None and not _from_folio(origin, headers):
                _log.warning("refused %s %s from origin %s", scope["method"], scope["path"], origin)
                await _error(403, _FOREIGN)(scope, receive, send)
                return
        await self._inner(scope, receive, send)


app.add_middleware(_OwnOrigin)


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
    if request.url.scheme == "https":
        return True
    # Behind Cloud Run the app sees plain http and the proxy says what the browser used.
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


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


@app.get("/sessions")
def get_sessions(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    session_id, account_id = opened
    try:
        rows = list_open_sessions(account_id)
    except Exception:
        return _down()
    return JSONResponse(
        content=[
            {
                "id": row[0],
                "created_at": row[1].isoformat(),
                "last_active_at": row[2].isoformat(),
                "current": row[0] == session_handle(session_id),
            }
            for row in rows
        ]
    )


@app.post("/sessions/{target_id}/end")
def end_account_session_route(request: Request, target_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    session_id, account_id = opened
    if len(target_id) != 64 or set(target_id) - set("0123456789abcdef"):
        return _error(404, _NOT_FOUND)
    try:
        ended = end_account_session(account_id, target_id)
    except Exception:
        return _down()
    if not ended:
        return _error(404, _NOT_FOUND)
    response = Response(status_code=204)
    if target_id == session_handle(session_id):
        _clear_session(response, request)
    return response


@app.get("/usage")
def get_usage(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        email, _ = load_account(account_id)
        with connect() as connection:
            totals = token_totals_for_email(connection, email)
    except Exception:
        return _down()
    return JSONResponse(
        content={
            "input_tokens": totals[0],
            "output_tokens": totals[1],
            "total_tokens": totals[2],
        }
    )


@app.delete("/account", status_code=204)
def remove_account(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    stop_account(account_id)
    try:
        firebase_uid = load_firebase_uid(account_id)
    except Exception:
        return _down()
    try:
        # The bytes go first: once the account row is gone nothing says whose they were.
        with connect() as connection:
            delete_account_bytes(open_storage(), account_id, connection)
    except Exception as error:
        _log.error("account %s files could not be removed: %s", account_id, type(error).__name__)
        return _error(502, _NOT_DELETED)
    if firebase_uid is not None:
        try:
            delete_firebase_user(firebase_uid)
        except SignInError:
            return _error(502, _NOT_DELETED)
    try:
        delete_account(account_id)
    except Exception:
        return _down()
    # A turn started from another tab during the Firebase call is stopped as well.
    stop_account(account_id)
    response = Response(status_code=204)
    _clear_session(response, request)
    return response


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


async def _time_zone(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    zone = body.get("time_zone")
    return zone if isinstance(zone, str) else None


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


async def _name_body(request: Request) -> dict[str, object] | None:
    raw = await request.body()
    if raw.strip() == b"":
        return {}
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict) or set(body) - {"name"}:
        return None
    return body


def _operate(
    account_id: uuid.UUID,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object] | JSONResponse:
    try:
        with connect() as connection:
            result = operations.call(
                connection,
                account_id,
                operations.Context(None),
                name,
                arguments,
            )
    except Exception:
        return _down()
    if result.get("ok") is True:
        return result
    if result.get("code") == "not_found":
        return _error(404, _NOT_FOUND)
    if arguments.keys() & {"name"} or name == "create_thread":
        return _error(400, operations.BAD_NAME)
    return _error(400, str(result.get("error")))


@app.get("/knowledge")
def get_knowledge(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = knowledge_file.list_files(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(
        content=[
            {
                "id": str(row[0]),
                "name": row[2],
                "byte_size": row[4],
                "updated_at": row[6].isoformat(),
            }
            for row in rows
        ]
    )


@app.get("/knowledge/{file_id}")
def get_knowledge_file(request: Request, file_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(file_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            row = knowledge_file.get_file_by_id(connection, account_id, parsed)
    except Exception:
        return _down()
    if row is None:
        return _error(404, _NOT_FOUND)
    return JSONResponse(
        content={
            "id": str(row[0]),
            "name": row[2],
            "body": row[3],
            "byte_size": row[4],
            "updated_at": row[6].isoformat(),
        }
    )


@app.get("/abilities")
def get_abilities(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = ability.list_abilities(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(
        content=[
            {"id": str(row[0]), "name": row[2], "body": row[3], "updated_at": row[5].isoformat()}
            for row in rows
        ]
    )


@app.get("/threads")
def get_threads(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = list_threads_with_latest(connection, account_id)
            working = run.in_flight_since(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(
        content=[
            {
                "id": str(row[0]),
                "name": row[1],
                "created_at": row[2].isoformat(),
                "working": row[0] in working,
                "working_since": working[row[0]][0].isoformat() if row[0] in working else None,
                "working_kind": working[row[0]][1] if row[0] in working else None,
                "latest_message_at": row[3].isoformat() if row[3] is not None else None,
            }
            for row in rows
        ]
    )


@app.get("/schedules")
def get_schedules(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = schedule.list_schedules(connection, account_id)
            names = {row[0]: row[2] for row in list_threads(connection, account_id)}
    except Exception:
        return _down()
    return JSONResponse(
        content=[
            {
                "id": str(row[0]),
                "task": row[3],
                "when_text": row[4],
                "next_run_at": row[5].isoformat() if row[5] is not None else None,
                "thread_id": str(row[2]),
                "thread_name": names.get(row[2], ""),
            }
            for row in rows
        ]
    )


_FILE_BAD = "Choose a file to upload."


def _file_item(row: tuple) -> dict[str, object]:
    return {
        "id": str(row[0]),
        "name": row[3],
        "kind": row[2],
        "content_type": row[4],
        "byte_size": row[5],
        "created_at": row[7].isoformat(),
    }


@app.get("/files")
def get_files(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = list_files(connection, account_id)
            used = byte_count(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(
        content={
            "files": [_file_item(row) for row in rows],
            "used_bytes": used,
            "limit_bytes": ACCOUNT_LIMIT_BYTES,
        }
    )


@app.post("/files")
async def post_file(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        body = await request.json()
    except Exception:
        return _error(400, _FILE_BAD)
    name = body.get("name") if isinstance(body, dict) else None
    size = body.get("size") if isinstance(body, dict) else None
    if not isinstance(name, str) or isinstance(size, bool) or not isinstance(size, int):
        return _error(400, _FILE_BAD)
    try:
        with connect() as connection:
            begun = begin_upload(connection, open_storage(), account_id, name, size)
    except FileRefused as refused:
        return _error(400, str(refused))
    except Exception:
        return _down()
    return JSONResponse(
        status_code=201,
        content={
            "id": str(begun.file_id),
            "upload": {
                "url": begun.url,
                "method": "PUT",
                "headers": {"Content-Type": begun.content_type},
            },
        },
    )


@app.post("/files/{file_id}/complete")
def post_file_complete(request: Request, file_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(file_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            row = complete_upload(connection, open_storage(), account_id, parsed)
    except FileRefused as refused:
        return _error(400, str(refused))
    except Exception:
        return _down()
    if row is None:
        return _error(404, _NOT_FOUND)
    return JSONResponse(content=_file_item(row))


@app.get("/files/{file_id}/download")
def download_file(request: Request, file_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(file_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            row = get_file(connection, account_id, parsed)
        if row is None:
            return _error(404, _NOT_FOUND)
        path = f"accounts/{account_id}/files/{row[0]}/{row[3]}"
        address = open_storage().download_url(path, row[3])
    except Exception:
        return _down()
    return RedirectResponse(address, status_code=302)


@app.get("/reports")
def get_reports(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            rows = report.list_reports(connection, account_id)
    except Exception:
        return _down()
    return JSONResponse(
        content=[
            {
                "id": str(row[0]),
                "name": row[4],
                "question": row[5],
                "created_at": row[11].isoformat(),
            }
            for row in rows
        ]
    )


def _load_report(
    request: Request, report_id: str
) -> tuple[report.ReportRow, list[report_source.SourceRow]] | JSONResponse:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(report_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            row = report.get_report(connection, account_id, parsed)
            sources = (
                report_source.list_sources(connection, account_id, parsed)
                if row is not None
                else []
            )
    except Exception:
        return _down()
    if row is None:
        return _error(404, _NOT_FOUND)
    return row, sources


@app.get("/reports/{report_id}")
def get_report(request: Request, report_id: str) -> Response:
    loaded = _load_report(request, report_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    row, sources = loaded
    return JSONResponse(
        content={
            "id": str(row[0]),
            "name": row[4],
            "question": row[5],
            "body": row[6],
            "stop_note": row[7],
            "created_at": row[11].isoformat(),
            "sources": [
                {"position": item[0], "kind": item[1], "title": item[2], "url": item[3],
                 "used_for": item[4]}
                for item in sources
            ],
        }
    )  # fmt: skip


@app.get("/reports/{report_id}/download")
def download_report(request: Request, report_id: str) -> Response:
    loaded = _load_report(request, report_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    row, sources = loaded
    return Response(
        content=report.render_download(row, sources),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{report.download_filename(row[4])}"'
        },
    )


@app.post("/threads")
async def post_thread(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    body = await _name_body(request)
    if body is None:
        return _error(400, operations.BAD_NAME)
    result = _operate(account_id, "create_thread", body)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(content=result["thread"])


@app.patch("/threads/{thread_id}")
async def patch_thread(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    if _parse_thread_id(thread_id) is None:
        return _error(404, _NOT_FOUND)
    body = await _name_body(request)
    if body is None or "name" not in body:
        return _error(400, operations.BAD_NAME)
    result = _operate(account_id, "rename_thread", {"thread_id": thread_id, **body})
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(content=result["thread"])


@app.delete("/threads/{thread_id}")
def delete_thread(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    if _parse_thread_id(thread_id) is None:
        return _error(404, _NOT_FOUND)
    result = _operate(account_id, "delete_thread", {"thread_id": thread_id})
    if isinstance(result, JSONResponse):
        return result
    return Response(status_code=204)


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


@app.get("/threads/{thread_id}/activity")
def thread_activity(request: Request, thread_id: str) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    parsed = _parse_thread_id(thread_id)
    if parsed is None:
        return _error(404, _NOT_FOUND)
    try:
        with connect() as connection:
            if get_thread(connection, account_id, parsed) is None:
                return _error(404, _NOT_FOUND)
            busy = run.in_flight_run(connection, account_id, parsed)
            lines = list_activity(connection, account_id, busy[0]) if busy is not None else []
    except Exception:
        return _down()
    return JSONResponse(
        content={
            "working": busy is not None,
            "kind": busy[1] if busy is not None else None,
            "since": busy[2].isoformat() if busy is not None else None,
            "items": [
                {"at": at.isoformat(), "kind": kind, "text": text, "url": url}
                for at, kind, text, url in lines
            ],
        }
    )


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
    time_zone = await _time_zone(request)
    try:
        with connect() as connection:
            started = start_reply(
                connection,
                session_id,
                account_id,
                parsed,
                text,
                open_model(),
                time_zone,
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
    time_zone = await _time_zone(request)
    try:
        with connect() as connection:
            started = start_retry(
                connection,
                session_id,
                account_id,
                parsed,
                open_model(),
                time_zone,
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


async def _audio_bytes(request: Request) -> bytes | None:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > transcribe.MAX_AUDIO_BYTES:
        return None
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > transcribe.MAX_AUDIO_BYTES:
            return None
    return bytes(audio)


@app.post("/voice/transcribe")
async def post_transcribe(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    content_type = transcribe.base_type(request.headers.get("content-type"))
    if content_type is None:
        return _error(400, transcribe.BAD_AUDIO_TYPE)
    audio = await _audio_bytes(request)
    if audio is None:
        return _error(413, transcribe.TOO_LONG)
    if audio == b"":
        return _error(400, transcribe.NO_AUDIO)
    try:
        email, _ = load_account(account_id)
        with connect() as connection:
            refused = transcribe.gate(connection, email)
    except Exception:
        return _down()
    if refused is not None:
        return _error(refused.status, refused.message)
    transcriber = open_transcriber()
    result = None
    try:
        result = await run_in_threadpool(transcriber.transcribe, audio, content_type)
    except Exception:
        _log.warning("transcription failed")
    try:
        with connect() as connection:
            transcribe.record(connection, account_id, email, transcriber, result)
    except Exception:
        return _down()
    if result is None:
        return _error(502, transcribe.FAILED)
    return JSONResponse(content={"text": result.text})


_DRIVE_STATE = "drive_state"
_DRIVE_STATE_SECONDS = 600
_DRIVE_PICK = "Choose a file in Google Drive."


def _frontend() -> str:
    return os.environ.get("FRONTEND_ORIGIN", "").strip().rstrip("/")


def _drive_done(request: Request, outcome: str) -> Response:
    response = RedirectResponse(f"{_frontend()}/?drive={outcome}", status_code=302)
    response.delete_cookie(
        key=_DRIVE_STATE, path="/", httponly=True, samesite="lax", secure=_https(request)
    )
    return response


@app.get("/integrations")
def get_integrations(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            saved = load_integration(connection, account_id, GOOGLE_DRIVE)
    except Exception:
        return _down()
    return JSONResponse(
        content={
            "drive": {
                "connected": saved is not None,
                "label": saved[1] if saved is not None else None,
                "picker_api_key": os.environ.get("DRIVE_PICKER_API_KEY", ""),
                "app_id": os.environ.get("DRIVE_APP_ID", ""),
            }
        }
    )


@app.get("/integrations/drive/connect")
def drive_connect(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    try:
        state = secrets.token_urlsafe(32)
        address = open_drive().consent_url(state)
    except Exception:
        return _drive_done(request, "failed")
    response = RedirectResponse(address, status_code=302)
    response.set_cookie(
        key=_DRIVE_STATE,
        value=state,
        max_age=_DRIVE_STATE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_https(request),
    )
    return response


@app.get("/integrations/drive/callback")
def drive_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> Response:
    # Google sends the browser here, so the person is known by the session cookie alone.
    expected = request.cookies.get(_DRIVE_STATE, "")
    session_id = _session_id(request)
    if (
        error != ""
        or code == ""
        or expected == ""
        or not secrets.compare_digest(expected.encode(), state.encode())
        or session_id is None
    ):
        return _drive_done(request, "failed")
    try:
        account_id = open_session(session_id)
        if account_id is None:
            return _drive_done(request, "failed")
        granted = open_drive().exchange_code(code)
        secret = open_kms().encrypt(granted.refresh_token.encode(), account_id.bytes)
        with connect() as connection:
            save_integration(connection, account_id, GOOGLE_DRIVE, secret, granted.email)
    except Exception as failure:
        _log.warning("drive connect failed: %s", type(failure).__name__)
        return _drive_done(request, "failed")
    return _drive_done(request, "connected")


def _drive_access(account_id: uuid.UUID) -> tuple[GoogleDrive, str, int] | JSONResponse:
    """A short-lived access token minted from the stored refresh token."""
    try:
        with connect() as connection:
            saved = load_integration(connection, account_id, GOOGLE_DRIVE)
        if saved is None:
            return _error(409, NOT_CONNECTED)
        drive = open_drive()
        refresh = open_kms().decrypt(saved[0], account_id.bytes).decode()
        token, expires = drive.refresh(refresh)
    except DriveReconnect:
        return _error(409, NOT_CONNECTED)
    except DriveError:
        return _error(502, DRIVE_DOWN)
    except Exception as failure:
        _log.warning("drive token failed: %s", type(failure).__name__)
        return _down()
    return drive, token, expires


@app.get("/integrations/drive/token")
def drive_token(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    access = _drive_access(account_id)
    if isinstance(access, JSONResponse):
        return access
    return JSONResponse(
        content={"access_token": access[1], "expires_in": access[2]},
        headers={"Cache-Control": "no-store"},
    )


def _import_drive_file(account_id: uuid.UUID, drive_file_id: str) -> Response:
    access = _drive_access(account_id)
    if isinstance(access, JSONResponse):
        return access
    drive, token, _ = access
    try:
        meta = drive.metadata(token, drive_file_id)
        name, content_type, export_as = plan_copy(meta, MAX_UPLOAD_BYTES)
        data = drive.fetch(token, drive_file_id, export_as, MAX_UPLOAD_BYTES)
        with connect() as connection:
            row = add_drive_copy(connection, open_storage(), account_id, name, content_type, data)
    except FileRefused as refused:
        return _error(400, str(refused))
    except DriveTooBig:
        return _error(400, TOO_BIG)
    except DriveMissing:
        return _error(400, CANNOT_OPEN)
    except DriveReconnect:
        return _error(409, NOT_CONNECTED)
    except DriveError:
        return _error(502, DRIVE_DOWN)
    except Exception:
        return _down()
    return JSONResponse(status_code=201, content=_file_item(row))


@app.post("/integrations/drive/import")
async def post_drive_import(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        body = await request.json()
    except Exception:
        return _error(400, _DRIVE_PICK)
    drive_file_id = body.get("file_id") if isinstance(body, dict) else None
    if not isinstance(drive_file_id, str) or not valid_file_id(drive_file_id):
        return _error(400, _DRIVE_PICK)
    return await run_in_threadpool(_import_drive_file, account_id, drive_file_id)


@app.delete("/integrations/drive", status_code=204)
def drive_disconnect(request: Request) -> Response:
    opened = _open_account(request)
    if isinstance(opened, JSONResponse):
        return opened
    _, account_id = opened
    try:
        with connect() as connection:
            saved = load_integration(connection, account_id, GOOGLE_DRIVE)
    except Exception:
        return _down()
    if saved is not None:
        try:
            refresh = open_kms().decrypt(saved[0], account_id.bytes).decode()
            open_drive().revoke(refresh)
        except Exception as failure:
            # The person asked to disconnect. A token Google would not revoke is still dropped.
            _log.warning("drive revoke failed: %s", type(failure).__name__)
    try:
        with connect() as connection:
            remove_integration(connection, account_id, GOOGLE_DRIVE)
    except Exception:
        return _down()
    return Response(status_code=204)
