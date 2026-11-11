import pytest

from transducer_dashboard.settings import DashboardSettings, SettingsError

ENV_VARS = (
    "READ_API_URL",
    "DASHBOARD_SECRET_KEY",
    "DASHBOARD_STATE_DIR",
    "DASHBOARD_SESSION_MAX_AGE",
    "DASHBOARD_DEFAULT_WINDOW_DAYS",
    "DASHBOARD_MAX_POINTS",
    "DASHBOARD_SHARED_EDITORS",
    "SITE_TIMEZONE",
    "DASHBOARD_COOKIE_SECURE",
    "DASHBOARD_REQUEST_TIMEOUT",
)


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_secret_is_required(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("DASHBOARD_STATE_DIR", str(tmp_path))

    with pytest.raises(SettingsError, match="DASHBOARD_SECRET_KEY"):
        DashboardSettings.from_env()


def test_secret_must_be_long_enough(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "short")
    monkeypatch.setenv("DASHBOARD_STATE_DIR", str(tmp_path))

    with pytest.raises(SettingsError, match="at least"):
        DashboardSettings.from_env()


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "x" * 24)
    monkeypatch.setenv("DASHBOARD_STATE_DIR", str(tmp_path))

    settings = DashboardSettings.from_env()

    assert settings.read_api_url == "http://read-api:8080"
    assert settings.state_dir == tmp_path
    assert settings.state_path == tmp_path / "state.json"
    assert settings.session_max_age == 28_800
    assert settings.default_window_days == 7
    assert settings.max_points == 2_000
    assert settings.shared_editors == frozenset()
    assert settings.cookie_secure is True


def test_parses_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "x" * 24)
    monkeypatch.setenv("DASHBOARD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("READ_API_URL", "http://read-api:9000/")
    monkeypatch.setenv("DASHBOARD_SESSION_MAX_AGE", "60")
    monkeypatch.setenv("DASHBOARD_DEFAULT_WINDOW_DAYS", "30")
    monkeypatch.setenv("DASHBOARD_MAX_POINTS", "500")
    monkeypatch.setenv("DASHBOARD_SHARED_EDITORS", "alice, bob,")
    monkeypatch.setenv("DASHBOARD_COOKIE_SECURE", "false")
    monkeypatch.setenv("DASHBOARD_REQUEST_TIMEOUT", "5")

    settings = DashboardSettings.from_env()

    assert settings.read_api_url == "http://read-api:9000"
    assert settings.session_max_age == 60
    assert settings.default_window_days == 30
    assert settings.max_points == 500
    assert settings.shared_editors == frozenset({"alice", "bob"})
    assert settings.cookie_secure is False
    assert settings.request_timeout == 5.0
