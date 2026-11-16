import pytest
from app.storage import FakeStorage, GcsStorage


def test_fake_storage_reads_what_was_put() -> None:
    storage = FakeStorage()
    storage.put("a/b", b"bytes")
    assert storage.read("a/b") == b"bytes"


def test_fake_storage_read_of_a_missing_path_is_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        FakeStorage().read("nope")


def test_gcs_storage_read_downloads_the_blob_and_maps_not_found() -> None:
    from google.api_core.exceptions import NotFound

    class _Blob:
        def __init__(self, found: bool) -> None:
            self._found = found

        def download_as_bytes(self) -> bytes:
            if not self._found:
                raise NotFound("gone")
            return b"hello"

    class _Bucket:
        def blob(self, path: str) -> _Blob:
            return _Blob(path == "here")

    storage = GcsStorage.__new__(GcsStorage)
    storage._bucket = _Bucket()
    assert storage.read("here") == b"hello"
    with pytest.raises(FileNotFoundError):
        storage.read("missing")
