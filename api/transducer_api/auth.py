"""Static bearer-token authentication for the read-only API."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TokenIdentity:
    label: str


class TokenRegistry:
    def __init__(self, tokens: Mapping[str, str]) -> None:
        self._tokens = dict(tokens)

    def authenticate(self, token: str) -> str | None:
        matched: str | None = None
        for label, expected in self._tokens.items():
            if secrets.compare_digest(token, expected):
                matched = label
        return matched

    def __len__(self) -> int:
        return len(self._tokens)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def make_bearer_dependency(
    registry: TokenRegistry,
) -> Callable[..., TokenIdentity]:
    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> TokenIdentity:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise unauthorized()
        label = registry.authenticate(credentials.credentials)
        if label is None:
            raise unauthorized()
        return TokenIdentity(label=label)

    return dependency
