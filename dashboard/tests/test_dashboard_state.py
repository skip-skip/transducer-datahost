import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from transducer_dashboard.state import (
    GRID_COLUMNS,
    MIN_FIGURE_HEIGHT,
    MIN_FIGURE_WIDTH,
    StateError,
    StateStore,
    default_state,
)


def make_figure(figure_id: str = "f1", **overrides: Any) -> dict[str, Any]:
    figure = {
        "id": figure_id,
        "title": "Figure",
        "location_id": None,
        "series_id": None,
        "range": "7d",
        "layout": {"x": 0, "y": 0, "w": MIN_FIGURE_WIDTH, "h": MIN_FIGURE_HEIGHT},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    figure.update(overrides)
    return figure


def make_scene(
    scene_id: str = "s1",
    figures: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    scene = {
        "id": scene_id,
        "name": "Scene 1",
        "figures": figures if figures is not None else [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    scene.update(overrides)
    return scene


def make_document(
    *scenes: dict[str, Any], ui: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"version": 2, "scenes": list(scenes), "ui": ui or {}}


def write_document(tmp_path, document: dict[str, Any]) -> None:
    (tmp_path / "state.json").write_text(json.dumps(document), encoding="utf-8")


def test_ensure_creates_default_v2_state(tmp_path) -> None:
    store = StateStore(tmp_path)

    store.ensure()

    assert (tmp_path / "state.json").exists()
    assert store.load() == default_state()
    assert default_state()["version"] == 2


def test_roundtrip_and_backup(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    state, _ = store.mutate(lambda data: data["scenes"].append(make_scene()))
    assert state["scenes"][0]["name"] == "Scene 1"

    store.mutate(lambda data: data["scenes"][0].update(name="Renamed"))

    assert store.load()["scenes"][0]["name"] == "Renamed"
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
    write_document(tmp_path, {"version": 99, "scenes": [], "ui": {}})

    with pytest.raises(StateError, match="unsupported state version"):
        store.load()


def test_rejects_malformed_scene_and_figure(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    write_document(
        tmp_path,
        make_document(make_scene(figures=[make_figure(figure_id="")])),
    )

    with pytest.raises(StateError, match="non-empty string"):
        store.load()


def test_rejects_duplicate_scene_and_figure_ids(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    write_document(
        tmp_path,
        make_document(
            make_scene("s1", figures=[make_figure("f1")]),
            make_scene("s2", figures=[make_figure("f1")]),
        ),
    )

    with pytest.raises(StateError, match="duplicate"):
        store.load()

    write_document(tmp_path, make_document(make_scene("s1"), make_scene("s1")))
    with pytest.raises(StateError, match="duplicate"):
        store.load()


def test_accepts_null_and_integer_series_scope(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    write_document(
        tmp_path,
        make_document(
            make_scene(
                figures=[
                    make_figure("f1"),
                    make_figure("f2", location_id=7, series_id=42),
                ]
            )
        ),
    )

    state = store.load()

    assert state["scenes"][0]["figures"][0]["location_id"] is None
    assert state["scenes"][0]["figures"][1]["location_id"] == 7
    assert state["scenes"][0]["figures"][1]["series_id"] == 42


def test_a_single_cell_is_a_valid_figure_size(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    write_document(
        tmp_path,
        make_document(make_scene(figures=[make_figure()])),
    )

    stored = store.load()

    assert stored["scenes"][0]["figures"][0]["layout"] == {
        "x": 0,
        "y": 0,
        "w": 1,
        "h": 1,
    }


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"location_id": "7"}, "location_id"),
        ({"series_id": 1.5}, "series_id"),
        ({"range": "1y"}, "range"),
        ({"layout": {"x": 0, "y": 0, "w": 0, "h": 2}}, "at least 1"),
        ({"layout": {"x": 0, "y": 0, "w": 3, "h": 0}}, "at least 1"),
        ({"layout": {"x": 10, "y": 0, "w": 3, "h": 2}}, "fit within"),
        ({"layout": {"x": -1, "y": 0, "w": 3, "h": 2}}, "non-negative"),
        ({"layout": {"x": 0, "y": 0, "w": 3}}, "missing keys"),
        ({"layout": {"x": 0, "y": 0, "w": 3, "h": 2, "z": 1}}, "unknown keys"),
    ],
)
def test_rejects_invalid_figure_fields(
    tmp_path, overrides: dict[str, Any], match: str
) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    write_document(
        tmp_path,
        make_document(make_scene(figures=[make_figure(**overrides)])),
    )

    with pytest.raises(StateError, match=match):
        store.load()


@pytest.mark.parametrize(
    ("ui", "match"),
    [
        ({"alice": {"view": "personal"}}, "unknown keys"),
        ({"alice": {"selected_scene_id": 7}}, "non-empty string or null"),
        ({"alice": {"scenes_tray_collapsed": "yes"}}, "boolean"),
        ({"": {}}, "non-empty strings"),
    ],
)
def test_rejects_invalid_ui(tmp_path, ui: dict[str, Any], match: str) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    write_document(tmp_path, make_document(ui=ui))

    with pytest.raises(StateError, match=match):
        store.load()


def test_version_1_document_is_archived_not_published(tmp_path) -> None:
    legacy = {
        "version": 1,
        "shared": {"figures": [{"id": "old", "name": "Old", "position": 0}]},
        "personal": {"alice": {"figures": [], "ui": {}}},
    }
    write_document(tmp_path, legacy)
    store = StateStore(tmp_path)

    state = store.load()

    assert state == default_state()
    archive = tmp_path / "state.v1.json"
    assert archive.exists()
    assert json.loads(archive.read_text(encoding="utf-8")) == legacy
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == state
    assert json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8")) == legacy


def test_ensure_archives_a_version_1_document(tmp_path) -> None:
    legacy = {"version": 1, "shared": {"figures": []}, "personal": {}}
    write_document(tmp_path, legacy)
    store = StateStore(tmp_path)

    store.ensure()

    assert (tmp_path / "state.v1.json").exists()
    assert store.load() == default_state()


def test_legacy_v2_document_is_archived_not_published(tmp_path) -> None:
    legacy = {
        "version": 2,
        "shared": {
            "scenes": [
                {
                    "id": "s1",
                    "name": "Old scene",
                    "position": 0,
                    "grid": {"cols": 12, "row_height": 220},
                    "figures": [],
                }
            ]
        },
        "personal": {"alice": {"scenes": []}},
    }
    write_document(tmp_path, legacy)
    store = StateStore(tmp_path)

    state = store.load()

    assert state == default_state()
    archive = tmp_path / "state.v2-legacy.json"
    assert archive.exists()
    assert json.loads(archive.read_text(encoding="utf-8")) == legacy
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == state
    assert json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8")) == legacy


def test_ensure_archives_a_legacy_v2_document(tmp_path) -> None:
    legacy = {"version": 2, "shared": {"scenes": []}, "personal": {}}
    write_document(tmp_path, legacy)
    store = StateStore(tmp_path)

    store.ensure()

    assert (tmp_path / "state.v2-legacy.json").exists()
    assert store.load() == default_state()


def test_ensure_rejects_an_invalid_v2_document(tmp_path) -> None:
    write_document(tmp_path, make_document(make_scene(name="")))
    store = StateStore(tmp_path)

    with pytest.raises(StateError, match="non-empty string"):
        store.ensure()


def test_concurrent_mutations_do_not_lose_updates(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    def add_scene(index: int) -> None:
        store.mutate(
            lambda data: data["scenes"].append(make_scene(f"s{index}", name=f"Scene {index}"))
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add_scene, range(16)))

    scenes = store.load()["scenes"]
    assert len(scenes) == 16
    assert {scene["id"] for scene in scenes} == {f"s{index}" for index in range(16)}


def test_mutate_does_not_persist_invalid_state(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    def break_it(data: dict[str, Any]) -> None:
        data["scenes"].append({"id": "x"})

    with pytest.raises(StateError):
        store.mutate(break_it)

    assert store.load() == default_state()


def test_default_document_is_valid() -> None:
    from transducer_dashboard.state import validate_state

    assert validate_state(default_state()) == default_state()
    assert GRID_COLUMNS == 12
