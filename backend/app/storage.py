import os
from datetime import timedelta
from typing import Protocol

# How long a signed address works. An upload of 50 MB on a slow line needs some time.
SIGNED_MINUTES = 30


class Storage(Protocol):
    def upload_url(self, path: str, content_type: str) -> str: ...

    def size(self, path: str) -> int | None: ...

    def read(self, path: str) -> bytes: ...

    def move(self, source: str, target: str) -> None: ...

    def delete(self, path: str) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def download_url(self, path: str, filename: str) -> str: ...


class FakeStorage:
    """Bytes in memory, for tests and for running without a bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_url(self, path: str, content_type: str) -> str:
        return f"https://storage.invalid/upload/{path}?type={content_type}"

    def put(self, path: str, data: bytes) -> None:
        # What the browser does with the signed address.
        self.objects[path] = data

    def has(self, path: str) -> bool:
        return path in self.objects

    def size(self, path: str) -> int | None:
        data = self.objects.get(path)
        return len(data) if data is not None else None

    def read(self, path: str) -> bytes:
        data = self.objects.get(path)
        if data is None:
            raise FileNotFoundError(path)
        return data

    def move(self, source: str, target: str) -> None:
        self.objects[target] = self.objects.pop(source)

    def delete(self, path: str) -> None:
        self.objects.pop(path, None)

    def delete_prefix(self, prefix: str) -> None:
        for path in [path for path in self.objects if path.startswith(prefix)]:
            del self.objects[path]

    def download_url(self, path: str, filename: str) -> str:
        return f"https://storage.invalid/download/{path}?name={filename}"


class GcsStorage:
    """The files bucket. Addresses are signed by the API's own service account over IAM."""

    def __init__(self, bucket_name: str) -> None:
        from google.cloud import storage

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def _signing(self) -> dict[str, str]:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        email = getattr(credentials, "service_account_email", None) or os.environ.get(
            "FILES_SIGNER_EMAIL", ""
        )
        return {"service_account_email": email, "access_token": credentials.token}

    def upload_url(self, path: str, content_type: str) -> str:
        return self._bucket.blob(path).generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=SIGNED_MINUTES),
            method="PUT",
            content_type=content_type,
            **self._signing(),
        )

    def size(self, path: str) -> int | None:
        blob = self._bucket.get_blob(path)
        return int(blob.size) if blob is not None and blob.size is not None else None

    def read(self, path: str) -> bytes:
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket.blob(path).download_as_bytes()
        except NotFound:
            raise FileNotFoundError(path) from None

    def move(self, source: str, target: str) -> None:
        blob =self._bucket.blob(source)
        self._bucket.copy_blob(blob, self._bucket, target)
        blob.delete()

    def delete(self, path: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(path).delete()
        except NotFound:
            pass

    def delete_prefix(self, prefix: str) -> None:
        for blob in self._client.list_blobs(self._bucket, prefix=prefix):
            blob.delete()

    def download_url(self, path: str, filename: str) -> str:
        safe = filename.replace('"', "")
        return self._bucket.blob(path).generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=SIGNED_MINUTES),
            method="GET",
            response_disposition=f'attachment; filename="{safe}"',
            **self._signing(),
        )


_LOCAL = FakeStorage()


def open_storage() -> Storage:
    bucket = os.environ.get("FILES_BUCKET", "").strip()
    if bucket == "":
        return _LOCAL
    return GcsStorage(bucket)
