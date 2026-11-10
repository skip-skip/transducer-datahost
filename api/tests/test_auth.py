import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from transducer_api.auth import TokenRegistry, make_bearer_dependency


def test_registry_matches_each_label() -> None:
    registry = TokenRegistry({"alice": "one", "bob": "two"})

    assert registry.authenticate("one") == "alice"
    assert registry.authenticate("two") == "bob"
    assert registry.authenticate("nope") is None
    assert len(registry) == 2


def test_dependency_accepts_valid_token() -> None:
    dependency = make_bearer_dependency(TokenRegistry({"alice": "one"}))
    identity = dependency(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="one")
    )

    assert identity.label == "alice"


def test_dependency_rejects_missing_token() -> None:
    dependency = make_bearer_dependency(TokenRegistry({"alice": "one"}))

    with pytest.raises(HTTPException) as excinfo:
        dependency(credentials=None)

    assert excinfo.value.status_code == 401
    assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}


def test_dependency_rejects_wrong_token() -> None:
    dependency = make_bearer_dependency(TokenRegistry({"alice": "one"}))

    with pytest.raises(HTTPException) as excinfo:
        dependency(
            credentials=HTTPAuthorizationCredentials(
                scheme="Bearer", credentials="wrong"
            )
        )

    assert excinfo.value.status_code == 401
