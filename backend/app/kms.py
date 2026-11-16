import os
from typing import Protocol


class Kms(Protocol):
    # The context is bound into the ciphertext, so a secret copied to another row will not open.
    def encrypt(self, plaintext: bytes, context: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, context: bytes) -> bytes: ...


class FakeKms:
    """Reversible and unreadable at a glance, for tests. It protects nothing."""

    def encrypt(self, plaintext: bytes, context: bytes) -> bytes:
        return b"fake-kms:" + context + b":" + plaintext[::-1]

    def decrypt(self, ciphertext: bytes, context: bytes) -> bytes:
        head = b"fake-kms:" + context + b":"
        if not ciphertext.startswith(head):
            raise ValueError("wrong context")
        return ciphertext[len(head) :][::-1]


class CloudKms:
    def __init__(self, key_name: str) -> None:
        from google.cloud import kms

        self._client = kms.KeyManagementServiceClient()
        self._key = key_name

    def encrypt(self, plaintext: bytes, context: bytes) -> bytes:
        response = self._client.encrypt(
            request={
                "name": self._key,
                "plaintext": plaintext,
                "additional_authenticated_data": context,
            }
        )
        return bytes(response.ciphertext)

    def decrypt(self, ciphertext: bytes, context: bytes) -> bytes:
        response = self._client.decrypt(
            request={
                "name": self._key,
                "ciphertext": ciphertext,
                "additional_authenticated_data": context,
            }
        )
        return bytes(response.plaintext)


def open_kms() -> Kms:
    # No local stand-in: a missing key must stop a connection, not store a token in the clear.
    key = os.environ.get("KMS_KEY", "").strip()
    if key == "":
        raise RuntimeError("KMS_KEY is not set")
    return CloudKms(key)
