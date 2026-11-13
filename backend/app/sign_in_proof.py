import os
from collections.abc import Mapping
from dataclasses import dataclass

import firebase_admin
from firebase_admin import auth, credentials

DEFAULT_SERVICE_ACCOUNT_PATH = "/Users/khamseaffan/Documents/Config/firebase-admin.json"

NOT_ALLOWED = "This email is not an allowed account."
NO_VERIFIED_EMAIL = "This sign-in has no verified email."
NOT_PROVEN = "This sign-in could not be verified."

_ALLOWED_EMAIL = "khamseaffan@gmail.com"
_ALLOWED_DOMAIN = "sakana.ai"


class SignInError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedSignIn:
    firebase_uid: str
    email: str


def service_account_path() -> str:
    configured = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH")
    if configured:
        return configured
    return DEFAULT_SERVICE_ACCOUNT_PATH


def email_allowed(email: str) -> bool:
    # Dots and plus-tags stay part of the address. khamse.affan@gmail.com is a different mailbox.
    if not isinstance(email, str):
        return False
    lowered = email.lower()
    if lowered == _ALLOWED_EMAIL:
        return True
    local, separator, domain = lowered.rpartition("@")
    if separator != "@" or not local or not domain or "@" in local:
        return False
    return domain == _ALLOWED_DOMAIN


def verify_allowed_sign_in(id_token: str) -> VerifiedSignIn:
    claims = _read_verified_token(id_token)
    email = claims.get("email")
    if not isinstance(email, str) or email == "" or claims.get("email_verified") is not True:
        raise SignInError(NO_VERIFIED_EMAIL)
    if not email_allowed(email):
        raise SignInError(NOT_ALLOWED)
    uid = claims.get("uid")
    if not isinstance(uid, str) or uid == "":
        raise SignInError(NOT_PROVEN)
    return VerifiedSignIn(firebase_uid=uid, email=email.lower())


def firebase_credential() -> credentials.Base | None:
    # On Cloud Run the service's own identity is used, so no key file is stored anywhere.
    if os.environ.get("FIREBASE_USE_ADC") == "1":
        return credentials.ApplicationDefault()
    path = service_account_path()
    if not os.path.isfile(path):
        return None
    return credentials.Certificate(path)


def _firebase_app() -> firebase_admin.App:
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass
    try:
        credential = firebase_credential()
    except Exception:
        raise SignInError(NOT_PROVEN) from None
    if credential is None:
        raise SignInError(NOT_PROVEN) from None
    try:
        return firebase_admin.initialize_app(credential)
    except ValueError:
        return firebase_admin.get_app()


def _read_verified_token(id_token: str) -> Mapping[str, object]:
    try:
        app = _firebase_app()
        claims = auth.verify_id_token(id_token, app=app)
    except SignInError:
        raise
    except Exception:
        # The later sign-in route can show this sentence. Provider text stays out of it.
        raise SignInError(NOT_PROVEN) from None
    if not isinstance(claims, Mapping):
        raise SignInError(NOT_PROVEN)
    return claims
