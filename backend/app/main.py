import os
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.account import ensure_account, load_account
from app.db import database_ready
from app.session import end_session, open_session, start_session
from app.sign_in_proof import NOT_ALLOWED, NOT_PROVEN, SignInError, verify_allowed_sign_in

app = FastAPI()

_CONTINUE = "Sign in to continue."
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
