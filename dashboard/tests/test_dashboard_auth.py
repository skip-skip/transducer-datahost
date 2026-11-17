from itsdangerous import URLSafeTimedSerializer

from transducer_dashboard.auth import COOKIE_NAME, COOKIE_SALT, SessionCodec


def test_session_roundtrip() -> None:
    codec = SessionCodec("x" * 32, 60)

    raw = codec.dump("alice", "read-token")

    session = codec.load(raw)
    assert session is not None
    assert session.label == "alice"
    assert session.token == "read-token"


def test_session_reads_the_cookie() -> None:
    codec = SessionCodec("x" * 32, 60)

    session = codec.from_cookies({COOKIE_NAME: codec.dump("bob", "token")})

    assert session is not None
    assert session.label == "bob"


def test_missing_cookie_has_no_session() -> None:
    codec = SessionCodec("x" * 32, 60)

    assert codec.from_cookies({}) is None
    assert codec.load(None) is None
    assert codec.load("") is None


def test_tampered_cookie_is_rejected() -> None:
    codec = SessionCodec("x" * 32, 60)
    raw = codec.dump("alice", "token")

    assert codec.load(raw + "x") is None
    assert codec.load("garbage") is None


def test_cookie_from_another_secret_is_rejected() -> None:
    first = SessionCodec("x" * 32, 60)
    second = SessionCodec("y" * 32, 60)

    assert second.load(first.dump("alice", "token")) is None


def test_expired_cookie_is_rejected() -> None:
    codec = SessionCodec("x" * 32, -1)

    assert codec.load(codec.dump("alice", "token")) is None


def test_payload_missing_fields_is_rejected() -> None:
    codec = SessionCodec("x" * 32, 60)

    raw = URLSafeTimedSerializer("x" * 32, salt=COOKIE_SALT).dumps({"label": "alice"})

    assert codec.load(raw) is None
