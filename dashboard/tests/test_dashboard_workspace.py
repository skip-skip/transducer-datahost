import pytest

from transducer_dashboard import scenes
from transducer_dashboard.auth import Session
from transducer_dashboard.settings import DashboardSettings
from transducer_dashboard.state import StateStore
from transducer_dashboard.workspace import DashboardShell


class FakeReadClient:
    def whoami(self, token: str) -> str:
        return "alice"


def make_shell(
    tmp_path, label: str = "alice", token: str = "good"
) -> tuple[DashboardShell, StateStore]:
    store = StateStore(tmp_path)
    store.ensure()
    settings = DashboardSettings(
        read_api_url="http://read-api:8080",
        secret_key="x" * 32,
        state_dir=tmp_path,
        cookie_secure=False,
    )
    shell = DashboardShell(
        store,
        FakeReadClient(),  # type: ignore[arg-type]
        Session(label=label, token=token),
        settings,
    )
    return shell, store


def test_empty_state_shows_the_create_prompt(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    assert shell.selected_scene_id is None
    assert shell.scene_buttons == {}
    assert "Create a scene" in shell.canvas_body.object
    assert "Select a figure" in shell.settings_body.objects[0].object
    assert shell.delete_scene_button.disabled is True
    assert shell.tray_body.visible is True


def test_create_scene_selects_and_persists_it(tmp_path) -> None:
    shell, store = make_shell(tmp_path)

    scene = shell.create_scene()

    assert scene is not None
    assert shell.selected_scene_id == scene["id"]
    assert shell.scene_buttons[scene["id"]].button_type == "primary"
    assert scene["name"] in shell.scene_title.object
    assert "0 figures" in shell.scene_summary.object
    assert "No figures yet" in shell.canvas_body.object

    stored = store.load()
    assert stored["scenes"][0]["id"] == scene["id"]
    assert stored["ui"]["alice"]["selected_scene_id"] == scene["id"]


def test_only_the_selected_scene_is_highlighted(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    first = shell.create_scene()
    second = shell.create_scene()

    assert first is not None
    assert second is not None
    assert shell.scene_buttons[first["id"]].button_type == "default"
    assert shell.scene_buttons[second["id"]].button_type == "primary"

    shell.select_scene(first["id"])

    assert shell.scene_buttons[first["id"]].button_type == "primary"
    assert shell.scene_buttons[second["id"]].button_type == "default"
    assert shell.selected_scene_id == first["id"]


def test_scenes_are_shared_but_selection_is_per_user(tmp_path) -> None:
    alice, _ = make_shell(tmp_path, label="alice")
    first = alice.create_scene()
    second = alice.create_scene()
    assert first is not None
    assert second is not None

    bob, store = make_shell(tmp_path, label="bob")
    assert set(bob.scene_buttons) == {first["id"], second["id"]}
    assert bob.selected_scene_id == first["id"]

    bob.select_scene(second["id"])

    alice_reloaded, _ = make_shell(tmp_path, label="alice")
    assert alice_reloaded.selected_scene_id == second["id"]
    stored = store.load()
    assert stored["ui"]["alice"]["selected_scene_id"] == second["id"]
    assert stored["ui"]["bob"]["selected_scene_id"] == second["id"]


def test_existing_scenes_default_to_the_first_scene(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    _, scene = store.mutate(lambda data: scenes.create_scene(data))

    shell, _ = make_shell(tmp_path)

    assert shell.selected_scene_id == scene["id"]
    assert store.load()["ui"]["alice"]["selected_scene_id"] == scene["id"]


def test_a_stale_selection_falls_back_to_the_first_scene(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    _, scene = store.mutate(lambda data: scenes.create_scene(data))
    store.mutate(
        lambda data: data["ui"].update(
            {
                "alice": {
                    "selected_scene_id": "gone",
                    "selected_figure_id": None,
                    "scenes_tray_collapsed": False,
                }
            }
        )
    )

    shell, _ = make_shell(tmp_path)

    assert shell.selected_scene_id == scene["id"]


def test_tray_collapse_persists(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()

    shell.toggle_scenes_tray()

    assert shell.tray_body.visible is False
    assert shell.tray_collapsed is True
    assert store.load()["ui"]["alice"]["scenes_tray_collapsed"] is True

    reloaded, _ = make_shell(tmp_path)
    assert reloaded.tray_body.visible is False


def test_delete_scene_requires_confirmation(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    scene = shell.create_scene()
    assert scene is not None

    shell.request_delete_scene()

    assert shell.confirm_delete_button is not None
    assert shell.cancel_delete_button is not None
    assert store.load()["scenes"][0]["id"] == scene["id"]

    shell.cancel_delete_scene()

    assert shell.confirm_delete_button is None
    assert shell.cancel_delete_button is None
    assert store.load()["scenes"][0]["id"] == scene["id"]


def test_confirm_delete_selects_the_next_scene(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    first = shell.create_scene()
    second = shell.create_scene()
    assert first is not None
    assert second is not None
    assert shell.selected_scene_id == second["id"]

    shell.request_delete_scene()
    shell.confirm_delete_scene()

    assert shell.selected_scene_id == first["id"]
    assert [scene["id"] for scene in store.load()["scenes"]] == [first["id"]]
    assert store.load()["ui"]["alice"]["selected_scene_id"] == first["id"]
    assert shell.scene_buttons[first["id"]].button_type == "primary"


def test_deleting_the_last_scene_returns_to_the_empty_state(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()

    shell.request_delete_scene()
    shell.confirm_delete_scene()

    assert shell.selected_scene_id is None
    assert shell.scene_buttons == {}
    assert "Create a scene" in shell.canvas_body.object
    assert store.load()["ui"]["alice"]["selected_scene_id"] is None


def test_deleting_a_scene_clears_another_users_selection(tmp_path) -> None:
    alice, store = make_shell(tmp_path, label="alice")
    scene = alice.create_scene()
    assert scene is not None
    bob, _ = make_shell(tmp_path, label="bob")
    bob.select_scene(scene["id"])

    alice.request_delete_scene()
    alice.confirm_delete_scene()

    stored = store.load()
    assert stored["ui"]["bob"]["selected_scene_id"] is None
    reloaded_bob, _ = make_shell(tmp_path, label="bob")
    assert reloaded_bob.selected_scene_id is None


def test_scene_buttons_are_wired(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.new_scene_button.clicks += 1
    first = shell.selected_scene_id
    assert first is not None
    assert len(shell.scene_buttons) == 1

    shell.new_scene_button.clicks += 1
    second = shell.selected_scene_id
    assert second is not None and second != first

    shell.scene_buttons[first].clicks += 1

    assert shell.selected_scene_id == first


def test_delete_and_cancel_buttons_are_wired(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.new_scene_button.clicks += 1
    scene_id = shell.selected_scene_id

    shell.delete_scene_button.clicks += 1
    assert shell.confirm_delete_button is not None

    assert shell.cancel_delete_button is not None
    shell.cancel_delete_button.clicks += 1
    assert shell.confirm_delete_button is None
    assert shell.selected_scene_id == scene_id

    shell.delete_scene_button.clicks += 1
    assert shell.confirm_delete_button is not None
    shell.confirm_delete_button.clicks += 1

    assert shell.selected_scene_id is None
    assert shell.scene_buttons == {}


def test_tray_toggle_is_wired(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.tray_toggle.clicks += 1
    assert shell.tray_collapsed is True

    shell.tray_toggle.clicks += 1
    assert shell.tray_collapsed is False


def test_scene_names_are_escaped_in_the_canvas(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    scene = shell.create_scene(name="<script>alert(1)</script>")

    assert scene is not None
    assert "<script>" not in shell.scene_title.object
    assert "&lt;script&gt;" in shell.scene_title.object
    assert shell.scene_buttons[scene["id"]].label == "<script>alert(1)</script>"


def test_selecting_an_unknown_scene_shows_an_error(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    shell.select_scene("missing")

    assert shell.status_pane.visible is True
    assert "selected scene not found" in shell.status_pane.object


def test_deleting_without_a_selected_scene_shows_an_error(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.request_delete_scene()

    assert shell.status_pane.visible is True
    assert "Select a scene to delete" in shell.status_pane.object


@pytest.mark.parametrize("label", ["alice", "bob"])
def test_actions_use_the_session_label(tmp_path, label: str) -> None:
    shell, store = make_shell(tmp_path, label=label)

    shell.create_scene()

    assert store.load()["ui"][label]["selected_scene_id"] is not None
