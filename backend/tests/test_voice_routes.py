import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from app.account import ensure_account
from app.db import connect
from app.main import app as public_app
from app.model import ModelError
from app.sign_in_proof import VerifiedSignIn
from app.transcribe import (
    BAD_AUDIO_TYPE,
    FAILED,
    NO_AUDIO,
    TOO_LONG,
    FakeTranscriber,
    OpenAITranscriber,
    open_transcriber,
)
from app.usage import record_usage, tokens_for_email
from fastapi.testclient import TestClient

STOPPED = "A system admin has stopped your account. Contact khamseaffan@gmail.com."
CAPPED = "You have reached the allowed usage limit."
AUDIO = b"\x1aE\xdf\xa3 not really audio"


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    email = f"voice-{uuid.uuid4().hex}@example.com"
    account_id = ensure_account(f"local:{email}", email)
    yield account_id, email
    with connect() as connection:
        connection.execute("DELETE FROM session WHERE account_id = %s", (account_id,))
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


def _signed_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(public_app, follow_redirects=False)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


def _use(monkeypatch: pytest.MonkeyPatch, fake: FakeTranscriber) -> FakeTranscriber:
    monkeypatch.setattr("app.main.open_transcriber", lambda: fake)
    return fake


def _rows(email: str) -> list[tuple]:
    with connect() as connection:
        return connection.execute(
            """
            SELECT operation, provider, model, input_tokens, output_tokens,
                   total_tokens, usage_missing, account_id, message_id, run_id
            FROM usage WHERE email = %s ORDER BY at
            """,
            (email,),
        ).fetchall()


def _post(client: TestClient, data: bytes = AUDIO, content_type: str = "audio/webm"):
    return client.post("/voice/transcribe", content=data, headers={"content-type": content_type})


def test_it_needs_a_session(monkeypatch) -> None:
    fake = _use(monkeypatch, FakeTranscriber(text="hi"))

    response = _post(TestClient(public_app))

    assert response.status_code == 401
    assert fake.heard == []


def test_it_returns_the_text_and_writes_one_measured_usage_row(account, monkeypatch) -> None:
    account_id, email = account
    fake = _use(monkeypatch, FakeTranscriber("hello there", 40, 3, 43))
    client = _signed_in(monkeypatch, email)

    response = _post(client)

    assert response.status_code == 200
    assert response.json() == {"text": "hello there"}
    assert fake.heard == [(AUDIO, "audio/webm")]
    rows = _rows(email)
    assert rows == [
        ("transcription", "openai", fake.model_id, 40, 3, 43, False, account_id, None, None)
    ]
    with connect() as connection:
        assert tokens_for_email(connection, email) == 43


def test_missing_provider_usage_is_stored_as_missing_never_zero(account, monkeypatch) -> None:
    _, email = account
    _use(monkeypatch, FakeTranscriber("hello"))
    client = _signed_in(monkeypatch, email)

    assert _post(client).status_code == 200

    row = _rows(email)[0]
    assert row[3:7] == (None, None, None, True)
    with connect() as connection:
        assert tokens_for_email(connection, email) == 0


def test_codec_parameters_are_ignored(account, monkeypatch) -> None:
    _, email = account
    fake = _use(monkeypatch, FakeTranscriber("ok", 1, 1, 2))
    client = _signed_in(monkeypatch, email)

    response = _post(client, content_type="audio/webm;codecs=opus")

    assert response.status_code == 200
    assert fake.heard[0][1] == "audio/webm"


@pytest.mark.parametrize(
    "kind", ["audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/mpeg"]
)
def test_every_listed_type_is_accepted(account, monkeypatch, kind) -> None:
    _, email = account
    _use(monkeypatch, FakeTranscriber("ok", 1, 1, 2))
    client = _signed_in(monkeypatch, email)

    assert _post(client, content_type=kind).status_code == 200


@pytest.mark.parametrize("kind", ["video/webm", "application/json", "text/plain", "audio/flac"])
def test_other_types_are_refused_before_any_model_call(account, monkeypatch, kind) -> None:
    _, email = account
    fake = _use(monkeypatch, FakeTranscriber("ok"))
    client = _signed_in(monkeypatch, email)

    response = _post(client, content_type=kind)

    assert response.status_code == 400
    assert response.json() == {"error": BAD_AUDIO_TYPE}
    assert fake.heard == []
    assert _rows(email) == []


def test_an_empty_recording_is_refused(account, monkeypatch) -> None:
    _, email = account
    fake = _use(monkeypatch, FakeTranscriber("ok"))
    client = _signed_in(monkeypatch, email)

    response = _post(client, data=b"")

    assert response.status_code == 400
    assert response.json() == {"error": NO_AUDIO}
    assert fake.heard == []
    assert _rows(email) == []


def test_a_recording_over_ten_megabytes_is_refused(account, monkeypatch) -> None:
    _, email = account
    fake = _use(monkeypatch, FakeTranscriber("ok"))
    client = _signed_in(monkeypatch, email)

    response = _post(client, data=b"a" * (10 * 1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json() == {"error": TOO_LONG}
    assert fake.heard == []
    assert _rows(email) == []


def test_exactly_ten_megabytes_is_accepted(account, monkeypatch) -> None:
    _, email = account
    _use(monkeypatch, FakeTranscriber("ok", 1, 1, 2))
    client = _signed_in(monkeypatch, email)

    assert _post(client, data=b"a" * (10 * 1024 * 1024)).status_code == 200


def test_a_stopped_account_gets_the_stopped_line_before_any_model_call(
    account, monkeypatch
) -> None:
    _, email = account
    fake = _use(monkeypatch, FakeTranscriber("ok"))
    client = _signed_in(monkeypatch, email)
    with connect() as connection:
        connection.execute("UPDATE email_limit SET stopped = true WHERE email = %s", (email,))

    response = _post(client)

    assert response.status_code == 403
    assert response.json() == {"error": STOPPED}
    assert fake.heard == []
    assert _rows(email) == []


def test_an_account_at_its_cap_gets_the_cap_line_before_any_model_call(
    account, monkeypatch
) -> None:
    account_id, email = account
    fake = _use(monkeypatch, FakeTranscriber("ok"))
    client = _signed_in(monkeypatch, email)
    with connect() as connection:
        connection.execute("UPDATE email_limit SET token_cap = 100 WHERE email = %s", (email,))
        record_usage(connection, account_id, email, "chat", "openai", "m", 60, 40, 100)

    response = _post(client)

    assert response.status_code == 403
    assert response.json() == {"error": CAPPED}
    assert fake.heard == []
    assert len(_rows(email)) == 1


def test_a_transcription_counts_toward_the_cap(account, monkeypatch) -> None:
    _, email = account
    _use(monkeypatch, FakeTranscriber("ok", 60, 40, 100))
    client = _signed_in(monkeypatch, email)
    with connect() as connection:
        connection.execute("UPDATE email_limit SET token_cap = 100 WHERE email = %s", (email,))

    assert _post(client).status_code == 200
    second = _post(client)

    assert second.status_code == 403
    assert second.json() == {"error": CAPPED}


def test_a_failed_call_says_so_and_records_missing_usage(account, monkeypatch) -> None:
    _, email = account
    _use(monkeypatch, FakeTranscriber(error=ModelError("provider-detail-quoll")))
    client = _signed_in(monkeypatch, email)

    response = _post(client)

    assert response.status_code == 502
    assert response.json() == {"error": FAILED}
    assert "quoll" not in response.text
    rows = _rows(email)
    assert len(rows) == 1
    assert rows[0][0] == "transcription"
    assert rows[0][3:7] == (None, None, None, True)


def test_the_audio_is_not_kept(account, monkeypatch, caplog) -> None:
    _, email = account
    marker = b"voice-marker-" + uuid.uuid4().hex.encode()
    _use(monkeypatch, FakeTranscriber("ok", 1, 1, 2))
    client = _signed_in(monkeypatch, email)

    with caplog.at_level("DEBUG"):
        assert _post(client, data=marker).status_code == 200

    assert marker.decode() not in caplog.text
    with connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        ]
        for table in tables:
            dumped = connection.execute(
                f'SELECT count(*) FROM "{table}" t WHERE t::text LIKE %s',
                (f"%{marker.decode()}%",),
            ).fetchone()
            assert dumped == (0,), table


class _Client:
    def __init__(self, answer=None, error: Exception | None = None) -> None:
        self.sent: list = []
        self._answer = answer
        self._error = error
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._answer


def test_openai_sends_the_audio_and_reads_usage() -> None:
    usage = SimpleNamespace(input_tokens=30, output_tokens=4, total_tokens=34)
    client = _Client(SimpleNamespace(text=" hello world \n", usage=usage))

    heard = OpenAITranscriber(client).transcribe(b"abc", "audio/mp4")

    assert (heard.text, heard.input_tokens, heard.output_tokens, heard.total_tokens) == (
        "hello world",
        30,
        4,
        34,
    )
    sent = client.sent[0]
    assert sent["model"] == "gpt-4o-mini-transcribe"
    assert sent["file"].name == "recording.mp4"
    assert sent["file"].read() == b"abc"


def test_openai_without_usage_returns_none_counts() -> None:
    client = _Client(SimpleNamespace(text="hi", usage=None))

    heard = OpenAITranscriber(client).transcribe(b"a", "audio/webm")

    assert (heard.text, heard.input_tokens, heard.output_tokens, heard.total_tokens) == (
        "hi",
        None,
        None,
        None,
    )


def test_openai_errors_become_model_errors_without_the_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key-123")
    client = _Client(error=RuntimeError("bad request with secret-key-123"))

    with pytest.raises(ModelError) as caught:
        OpenAITranscriber(client).transcribe(b"a", "audio/webm")

    assert "secret-key-123" not in str(caught.value)


def test_without_a_key_a_fake_transcriber_is_used(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert isinstance(open_transcriber(), FakeTranscriber)


def test_with_a_key_the_openai_transcriber_is_used(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    opened = open_transcriber()

    assert isinstance(opened, OpenAITranscriber)
    assert opened.provider == "openai"
