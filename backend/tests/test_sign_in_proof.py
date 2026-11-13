import firebase_admin
import pytest
from app.sign_in_proof import (
    SignInError,
    email_allowed,
    service_account_path,
    verify_allowed_sign_in,
)

ADMIN_FILE = "/Users/khamseaffan/Documents/Config/firebase-admin.json"
TOKEN_UID = "firebase-user-1"
NOT_ALLOWED = "This email is not an allowed account."
NO_VERIFIED_EMAIL = "This sign-in has no verified email."
NOT_PROVEN = "This sign-in could not be verified."

ALLOWED_EMAILS = [
    "khamseaffan@gmail.com",
    "KhamseAffan@Gmail.com",
    "person@sakana.ai",
    "user.name+tag@sakana.ai",
    "User.Name+Tag@Sakana.AI",
]

REFUSED_EMAILS = [
    "other@gmail.com",
    "khamse.affan@gmail.com",
    "khamseaffan+tag@gmail.com",
    "user@sub.sakana.ai",
    "user@sakana.ai.evil.com",
    "user@evilsakana.ai",
    "user@name@sakana.ai",
    "not-an-email",
    "@sakana.ai",
    "user@",
]


def use_token(monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]) -> None:
    monkeypatch.setattr("app.sign_in_proof._read_verified_token", lambda _token: claims)


def test_import_does_not_initialize_firebase():
    with pytest.raises(ValueError):
        firebase_admin.get_app()


def test_service_account_path_defaults_to_the_admin_file(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_PATH", raising=False)

    assert service_account_path() == ADMIN_FILE


def test_service_account_path_uses_the_env_var_when_set(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", "/tmp/folio-firebase-admin.json")

    assert service_account_path() == "/tmp/folio-firebase-admin.json"


@pytest.mark.parametrize("email", ALLOWED_EMAILS)
def test_allowed_verified_email_returns_the_token_uid(monkeypatch, email: str):
    use_token(
        monkeypatch,
        {"uid": TOKEN_UID, "email": email, "email_verified": True},
    )

    result = verify_allowed_sign_in("token")

    assert email_allowed(email)
    assert result.firebase_uid == TOKEN_UID
    assert result.email == email.lower()
    assert result.firebase_uid != f"local:{result.email}"


@pytest.mark.parametrize("email", REFUSED_EMAILS)
def test_disallowed_verified_email_uses_the_allowlist_refusal(monkeypatch, email: str):
    use_token(
        monkeypatch,
        {"uid": TOKEN_UID, "email": email, "email_verified": True},
    )

    assert email_allowed(email) is False
    with pytest.raises(SignInError) as caught:
        verify_allowed_sign_in("token")

    assert str(caught.value) == NOT_ALLOWED


@pytest.mark.parametrize(
    "claims",
    [
        {"uid": TOKEN_UID, "email": "khamseaffan@gmail.com", "email_verified": False},
        {"uid": TOKEN_UID, "email": "khamseaffan@gmail.com"},
        {"uid": TOKEN_UID, "email": "khamseaffan@gmail.com", "email_verified": "true"},
        {"uid": TOKEN_UID, "email": "other@gmail.com", "email_verified": False},
        {"uid": TOKEN_UID, "email_verified": True},
        {"uid": TOKEN_UID, "email": "", "email_verified": True},
        {"uid": TOKEN_UID, "email": None, "email_verified": True},
    ],
)
def test_missing_or_unverified_email_is_refused(monkeypatch, claims: dict[str, object]):
    use_token(monkeypatch, claims)

    with pytest.raises(SignInError) as caught:
        verify_allowed_sign_in("token")

    assert str(caught.value) == NO_VERIFIED_EMAIL


def test_verified_email_without_a_uid_is_refused(monkeypatch):
    use_token(
        monkeypatch,
        {"email": "person@sakana.ai", "email_verified": True},
    )

    with pytest.raises(SignInError) as caught:
        verify_allowed_sign_in("token")

    assert str(caught.value) == NOT_PROVEN


def test_missing_credential_file_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(tmp_path / "missing.json"))

    def forbid(*_args, **_kwargs):
        raise AssertionError("Firebase should not be called")

    monkeypatch.setattr("app.sign_in_proof.auth.verify_id_token", forbid)
    monkeypatch.setattr("app.sign_in_proof.firebase_admin.initialize_app", forbid)

    with pytest.raises(SignInError) as caught:
        verify_allowed_sign_in("token")

    assert str(caught.value) == NOT_PROVEN
    with pytest.raises(ValueError):
        firebase_admin.get_app()


def test_unreadable_credential_file_fails_closed(monkeypatch, tmp_path):
    path = tmp_path / "firebase-admin.json"
    path.write_text('{"type": "service_account"}', encoding="utf-8")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(path))

    with pytest.raises(SignInError) as caught:
        verify_allowed_sign_in("token")

    message = str(caught.value)
    assert message == NOT_PROVEN
    assert "service_account" not in message
    with pytest.raises(ValueError):
        firebase_admin.get_app()
