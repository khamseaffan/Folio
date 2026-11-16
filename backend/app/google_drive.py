import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.file import BAD_FILE_TYPE, EMPTY_FILE, TOO_BIG, TYPES, FileRefused, _clean_name

# Only files the person picks for Folio. The email scopes are for the label.
SCOPE = "openid email https://www.googleapis.com/auth/drive.file"
_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

_PDF = "application/pdf"
_XLSX = TYPES[".xlsx"]
# Google's own formats have no bytes of their own, so Google converts them.
EXPORTS = {
    "application/vnd.google-apps.document": (_PDF, ".pdf"),
    "application/vnd.google-apps.presentation": (_PDF, ".pdf"),
    "application/vnd.google-apps.spreadsheet": (_XLSX, ".xlsx"),
}
_EXTENSION_OF_TYPE = {content_type: extension for extension, content_type in TYPES.items()}

_ID = re.compile(r"^[A-Za-z0-9_-]{5,128}$")

NOT_CONNECTED = "Connect Google Drive first."
CANNOT_OPEN = "Folio cannot open that file. Pick it again in Google Drive."
DRIVE_DOWN = "Google Drive did not answer. Try again."


class DriveError(Exception):
    """Google did not do what was asked."""


class DriveReconnect(DriveError):
    """The saved permission no longer works, so the person must connect again."""


class DriveMissing(DriveError):
    """The file is gone, or Folio was never given it."""


class DriveTooBig(DriveError):
    """The file passed the size limit."""


@dataclass(frozen=True)
class Granted:
    refresh_token: str
    email: str | None


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    size: int | None


class GoogleDrive(Protocol):
    def consent_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> Granted: ...

    def refresh(self, refresh_token: str) -> tuple[str, int]: ...

    def metadata(self, access_token: str, file_id: str) -> DriveFile: ...

    def fetch(
        self, access_token: str, file_id: str, export_as: str | None, limit: int
    ) -> bytes: ...

    def revoke(self, token: str) -> None: ...


def valid_file_id(file_id: str) -> bool:
    return _ID.match(file_id) is not None


def plan_copy(meta: DriveFile, limit: int) -> tuple[str, str, str | None]:
    """The stored name, its content type, and what to ask Google to export, or a refusal."""
    name = _clean_name(meta.name)
    export = EXPORTS.get(meta.mime_type)
    if export is not None:
        export_as, extension = export
        stem = name if name != "" else "Drive file"
        if not stem.lower().endswith(extension):
            stem += extension
        return stem, export_as, export_as
    if meta.mime_type.startswith("application/vnd.google-apps."):
        raise FileRefused(BAD_FILE_TYPE)
    suffix = Path(name).suffix.lower() if name != "" else ""
    content_type = TYPES.get(suffix)
    if content_type is None:
        # A file named without its ending still has a type in Drive.
        extension = _EXTENSION_OF_TYPE.get(meta.mime_type)
        if extension is None:
            raise FileRefused(BAD_FILE_TYPE)
        name = (name if name != "" else "Drive file") + extension
        content_type = TYPES[extension]
    if meta.size is not None:
        if meta.size < 1:
            raise FileRefused(EMPTY_FILE)
        if meta.size > limit:
            raise FileRefused(TOO_BIG)
    return name, content_type, None


class FakeDrive:
    """Google in memory, for tests. Nothing here reaches the network."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[DriveFile, bytes]] = {}
        self.refresh_token = "refresh-token-secret"
        self.email: str | None = "person@example.com"
        self.code_ok = True
        self.refresh_ok = True
        self.revoke_ok = True
        self.revoked: list[str] = []
        self.refreshed: list[str] = []

    def add(self, file_id: str, name: str, mime_type: str, data: bytes) -> None:
        self.files[file_id] = (DriveFile(file_id, name, mime_type, len(data)), data)

    def consent_url(self, state: str) -> str:
        return "https://accounts.invalid/consent?" + urlencode({"state": state})

    def exchange_code(self, code: str) -> Granted:
        if not self.code_ok or code != "good-code":
            raise DriveError("code refused")
        return Granted(self.refresh_token, self.email)

    def refresh(self, refresh_token: str) -> tuple[str, int]:
        self.refreshed.append(refresh_token)
        if not self.refresh_ok or refresh_token != self.refresh_token:
            raise DriveReconnect("refused")
        return "access-token-secret", 3599

    def metadata(self, access_token: str, file_id: str) -> DriveFile:
        found = self.files.get(file_id)
        if found is None:
            raise DriveMissing("missing")
        return found[0]

    def fetch(self, access_token: str, file_id: str, export_as: str | None, limit: int) -> bytes:
        found = self.files.get(file_id)
        if found is None:
            raise DriveMissing("missing")
        if len(found[1]) > limit:
            raise DriveTooBig("too big")
        return found[1]

    def revoke(self, token: str) -> None:
        if not self.revoke_ok:
            raise DriveError("revoke failed")
        self.revoked.append(token)


class HttpDrive:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def consent_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
        return f"{_AUTH_URL}?{query}"

    def _token(self, form: dict[str, str]) -> dict:
        try:
            response = httpx.post(
                _TOKEN_URL,
                data={"client_id": self._client_id, "client_secret": self._client_secret, **form},
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise DriveError(type(error).__name__) from None
        if response.status_code != 200:
            reason = ""
            try:
                reason = str(response.json().get("error", ""))
            except ValueError:
                pass
            if reason == "invalid_grant":
                raise DriveReconnect("invalid_grant")
            raise DriveError(f"token endpoint {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise DriveError("token endpoint body")
        return body

    def exchange_code(self, code: str) -> Granted:
        body = self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            }
        )
        refresh = body.get("refresh_token")
        access = body.get("access_token")
        if not isinstance(refresh, str) or not isinstance(access, str) or refresh == "":
            raise DriveError("no refresh token")
        # The person can untick the Drive box on Google's page, and the grant is then useless.
        if _DRIVE_FILE_SCOPE not in str(body.get("scope", "")).split():
            raise DriveError("drive scope not granted")
        return Granted(refresh, self._email(access))

    def _email(self, access_token: str) -> str | None:
        try:
            response = httpx.get(
                _USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
            email = response.json().get("email") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return email if isinstance(email, str) and email != "" else None

    def refresh(self, refresh_token: str) -> tuple[str, int]:
        body = self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})
        access = body.get("access_token")
        expires = body.get("expires_in")
        if not isinstance(access, str) or isinstance(expires, bool) or not isinstance(expires, int):
            raise DriveError("token body")
        return access, expires

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    def metadata(self, access_token: str, file_id: str) -> DriveFile:
        try:
            response = httpx.get(
                f"{_FILES_URL}/{file_id}",
                params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"},
                headers=self._headers(access_token),
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise DriveError(type(error).__name__) from None
        if response.status_code in (403, 404):
            raise DriveMissing(str(response.status_code))
        if response.status_code != 200:
            raise DriveError(f"files {response.status_code}")
        body = response.json()
        name, mime = body.get("name"), body.get("mimeType")
        if not isinstance(name, str) or not isinstance(mime, str):
            raise DriveError("files body")
        raw = body.get("size")
        size = int(raw) if isinstance(raw, str) and raw.isdigit() else None
        return DriveFile(file_id, name, mime, size)

    def fetch(self, access_token: str, file_id: str, export_as: str | None, limit: int) -> bytes:
        if export_as is None:
            url, params = f"{_FILES_URL}/{file_id}", {"alt": "media"}
        else:
            url, params = f"{_FILES_URL}/{file_id}/export", {"mimeType": export_as}
        params["supportsAllDrives"] = "true"
        chunks: list[bytes] = []
        total = 0
        try:
            with httpx.stream(
                "GET",
                url,
                params=params,
                headers=self._headers(access_token),
                timeout=_TIMEOUT,
            ) as response:
                if response.status_code in (403, 404):
                    raise DriveMissing(str(response.status_code))
                if response.status_code != 200:
                    raise DriveError(f"download {response.status_code}")
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    # The size in the metadata can be wrong or absent, so the stream is counted.
                    if total > limit:
                        raise DriveTooBig("too big")
                    chunks.append(chunk)
        except httpx.HTTPError as error:
            raise DriveError(type(error).__name__) from None
        return b"".join(chunks)

    def revoke(self, token: str) -> None:
        try:
            httpx.post(_REVOKE_URL, data={"token": token}, timeout=_TIMEOUT)
        except httpx.HTTPError as error:
            raise DriveError(type(error).__name__) from None


def open_drive() -> GoogleDrive:
    client_id = os.environ.get("DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DRIVE_CLIENT_SECRET", "").strip()
    origin = os.environ.get("FRONTEND_ORIGIN", "").strip().rstrip("/")
    if client_id == "" or client_secret == "" or origin == "":
        raise RuntimeError("Google Drive is not configured")
    return HttpDrive(client_id, client_secret, origin + "/api/integrations/drive/callback")
