import json

import pytest

from transducer_dashboard.state import StateError, StateStore, default_state


def test_ensure_creates_default_state(tmp_path) -> None:
    store = StateStore(tmp_path)

    store.ensure()

    assert (tmp_path / "state.json").exists()
    assert store.load() == default_state()


def test_roundtrip_and_backup(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    state, _ = store.mutate(
        lambda data: data["shared"]["figures"].append(
            {
                "id": "abc",
                "name": "Figure",
                "series_id": 1,
                "position": 0,
                "config": {},
            }
        )
    )
    assert state["shared"]["figures"][0]["name"] == "Figure"

    store.mutate(lambda data: data["shared"]["figures"][0].update(name="Renamed"))

    assert store.load()["shared"]["figures"][0]["name"] == "Renamed"
    assert (tmp_path / "state.json.bak").exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_rejects_invalid_json(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(StateError, match="not valid JSON"):
        store.load()


def test_rejects_unknown_version(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    (tmp_path / "state.json").write_text(
        json.dumps({"version": 99, "shared": {"figures": []}, "personal": {}}),
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="unsupported state version"):
        store.load()


def test_rejects_malformed_figure(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "shared": {"figures": [{"id": "", "name": "x"}]},
                "personal": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="must be a non-empty string"):
        store.load()


def test_mutate_does_not_persist_invalid_state(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    def break_it(data):
        data["shared"]["figures"].append({"id": "x", "name": ""})

    with pytest.raises(StateError):
        store.mutate(break_it)

    assert store.load() == default_state()
