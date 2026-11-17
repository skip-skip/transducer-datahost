import json

import pytest

from transducer_dashboard.state import (
    DEFAULT_SCENE_NAME,
    STATE_VERSION,
    StateError,
    StateStore,
    default_state,
    initial_layout,
)


def v1_state(**overrides):
    state = {
        "version": 1,
        "shared": {"figures": []},
        "personal": {},
    }
    state.update(overrides)
    return state


def test_ensure_creates_default_state(tmp_path) -> None:
    store = StateStore(tmp_path)

    store.ensure()

    assert (tmp_path / "state.json").exists()
    assert store.load() == default_state()
    assert store.load()["version"] == STATE_VERSION == 2


def test_roundtrip_and_backup(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    def add_scene(data):
        data["shared"]["scenes"].append(
            {
                "id": "scene-1",
                "name": "Operations",
                "position": 0,
                "grid": {"cols": 12, "row_height": 220},
                "figures": [
                    {
                        "id": "abc",
                        "name": "Figure",
                        "series_id": 1,
                        "location_id": None,
                        "config": {},
                        "layout": {"x": 0, "y": 0, "w": 6, "h": 2},
                    }
                ],
                "created_by": "alice",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )

    state, _ = store.mutate(add_scene)
    assert state["shared"]["scenes"][0]["name"] == "Operations"

    store.mutate(
        lambda data: data["shared"]["scenes"][0]["figures"][0].update(name="Renamed")
    )

    assert store.load()["shared"]["scenes"][0]["figures"][0]["name"] == "Renamed"
    assert (tmp_path / "state.json.bak").exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_v2_backup_restores_previous_document(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    def add_scene(scene_id: str, name: str):
        def operation(data):
            data["shared"]["scenes"].append(
                {
                    "id": scene_id,
                    "name": name,
                    "position": 0,
                    "grid": {"cols": 12, "row_height": 220},
                    "figures": [],
                    "created_by": "alice",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            )

        return operation

    store.mutate(add_scene("first", "First"))
    store.mutate(add_scene("second", "Second"))

    backup = json.loads(
        (tmp_path / "state.json.bak").read_text(encoding="utf-8")
    )
    assert backup["version"] == STATE_VERSION
    assert [scene["id"] for scene in backup["shared"]["scenes"]] == ["first"]

    # Restore the previous document as an operator would while the service is
    # stopped, then start a fresh store on the same directory.
    (tmp_path / "state.json").write_text(json.dumps(backup), encoding="utf-8")

    restored = StateStore(tmp_path).load()
    assert restored == backup
    assert restored["version"] == STATE_VERSION
    assert [scene["id"] for scene in restored["shared"]["scenes"]] == ["first"]


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


def test_rejects_layout_outside_the_grid(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    state = default_state()
    state["shared"]["scenes"].append(
        {
            "id": "scene-1",
            "name": "Broken",
            "position": 0,
            "grid": {"cols": 12, "row_height": 220},
            "figures": [
                {
                    "id": "figure-1",
                    "name": "Too wide",
                    "series_id": None,
                    "location_id": None,
                    "config": {},
                    "layout": {"x": 6, "y": 0, "w": 8, "h": 2},
                }
            ],
        }
    )
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(StateError, match="extends past 12 grid columns"):
        store.load()


def test_migrates_v1_state_into_a_default_scene(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    legacy = v1_state(
        shared={
            "figures": [
                {
                    "id": "shared-figure",
                    "name": "Shared level",
                    "series_id": 42,
                    "location_id": 7,
                    "position": 0,
                    "config": {"range": "24h"},
                    "created_by": "alice",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
        personal={
            "alice": {
                "figures": [
                    {
                        "id": "personal-figure",
                        "name": "My level",
                        "series_id": 43,
                        "position": 0,
                        "config": {},
                    }
                ],
                "ui": {
                    "view": "shared",
                    "personal": {
                        "selected_figure_id": "personal-figure",
                        "folders_collapsed": True,
                    },
                    "shared": {"selected_figure_id": "shared-figure"},
                    "tz": "utc",
                },
            }
        },
    )
    (tmp_path / "state.json").write_text(json.dumps(legacy), encoding="utf-8")

    state = store.load()

    assert state["version"] == STATE_VERSION
    shared_scene = state["shared"]["scenes"][0]
    assert shared_scene["name"] == DEFAULT_SCENE_NAME
    assert shared_scene["grid"] == {"cols": 12, "row_height": 220}
    figure = shared_scene["figures"][0]
    assert figure["id"] == "shared-figure"
    assert figure["series_id"] == 42
    assert figure["location_id"] == 7
    assert figure["config"] == {"range": "24h"}
    assert figure["layout"] == initial_layout(0)
    assert figure["created_by"] == "alice"

    alice = state["personal"]["alice"]
    personal_scene = alice["scenes"][0]
    assert personal_scene["figures"][0]["id"] == "personal-figure"
    assert alice["ui"]["view"] == "shared"
    assert alice["ui"]["personal"] == {
        "selected_scene_id": personal_scene["id"],
        "selected_figure_id": "personal-figure",
        "folders_collapsed": True,
        "options_collapsed": False,
    }
    assert alice["ui"]["shared"] == {
        "selected_scene_id": shared_scene["id"],
        "selected_figure_id": "shared-figure",
        "folders_collapsed": False,
        "options_collapsed": False,
    }
    assert alice["ui"]["tz"] == "utc"

    # The migration is written through the normal atomic path and keeps a
    # backup of the original document.
    assert json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8")) == legacy
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == state


def test_migration_is_stable_across_reloads(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    legacy = v1_state(
        personal={
            "alice": {
                "figures": [
                    {"id": "f1", "name": "One", "series_id": 1, "config": {}},
                    {"id": "f2", "name": "Two", "series_id": 2, "config": {}},
                ]
            }
        }
    )
    (tmp_path / "state.json").write_text(json.dumps(legacy), encoding="utf-8")

    first = store.load()
    second = store.load()

    assert first == second
    scene = first["personal"]["alice"]["scenes"][0]
    assert [figure["id"] for figure in scene["figures"]] == ["f1", "f2"]
    assert [figure["layout"] for figure in scene["figures"]] == [
        initial_layout(0),
        initial_layout(1),
    ]


def test_migration_keeps_unscoped_figures_unscoped(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    legacy = v1_state(
        shared={
            "figures": [
                {
                    "id": "legacy",
                    "name": "Legacy",
                    "series_id": 42,
                    "position": 0,
                    "config": {},
                }
            ]
        }
    )
    (tmp_path / "state.json").write_text(json.dumps(legacy), encoding="utf-8")

    state = store.load()

    assert state["shared"]["scenes"][0]["figures"][0]["location_id"] is None


def test_accepts_optional_location_id(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    (tmp_path / "state.json").write_text(
        json.dumps(
            v1_state(
                shared={
                    "figures": [
                        {
                            "id": "abc",
                            "name": "Figure",
                            "series_id": 1,
                            "location_id": 7,
                            "position": 0,
                            "config": {},
                        }
                    ]
                }
            )
        ),
        encoding="utf-8",
    )

    state = store.load()

    figure = state["shared"]["scenes"][0]["figures"][0]
    assert figure["location_id"] == 7


def test_rejects_non_integer_location_id(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    (tmp_path / "state.json").write_text(
        json.dumps(
            v1_state(
                shared={
                    "figures": [
                        {
                            "id": "abc",
                            "name": "Figure",
                            "series_id": 1,
                            "location_id": "7",
                            "position": 0,
                            "config": {},
                        }
                    ]
                }
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="location_id"):
        store.load()


def test_mutate_does_not_persist_invalid_state(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    def break_it(data):
        data["shared"]["scenes"].append({"id": "x", "name": ""})

    with pytest.raises(StateError):
        store.mutate(break_it)

    assert store.load() == default_state()
