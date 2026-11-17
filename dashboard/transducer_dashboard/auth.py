"""Signed read-token session shared by FastAPI and the Panel application."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "transducer_dashboard"
COOKIE_SALT = "transducer-dashboard-session"


@dataclass(frozen=True)
class Session:
    label: str
    token: str


class SessionCodec:
    """Sign, verify, and decode the dashboard session cookie."""

    def __init__(self, secret_key: str, max_age: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=COOKIE_SALT)
        self._max_age = max_age

    def dump(self, label: str, token: str) -> str:
        return self._serializer.dumps({"label": label, "token": token})

    def load(self, raw: str | None) -> Session | None:
        if not raw:
            return None
        try:
            payload = self._serializer.loads(raw, max_age=self._max_age)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(payload, dict):
            return None
        label = payload.get("label")
        token = payload.get("token")
        if not isinstance(label, str) or not label:
            return None
        if not isinstance(token, str) or not token:
            return None
        return Session(label=label, token=token)

    def from_cookies(self, cookies: Mapping[str, str]) -> Session | None:
        return self.load(cookies.get(COOKIE_NAME))
