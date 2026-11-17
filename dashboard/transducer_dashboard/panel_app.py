"""Panel frontend mounted inside the FastAPI dashboard service."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import panel as pn

from .auth import SessionCodec
from .read_client import ReadClient
from .settings import DashboardSettings
from .state import StateStore
from .workspace import DashboardShell


def _sign_in_view() -> pn.Column:
    return pn.Column(
        pn.pane.Markdown("# Transducer dashboard"),
        pn.pane.HTML(
            "<p>Sign in with a read API token to open the dashboard.</p>"
            '<p><a href="login">Sign in</a></p>'
        ),
    )


def build_view(
    cookies: Mapping[str, str],
    codec: SessionCodec,
    store: StateStore,
    client: ReadClient,
    settings: DashboardSettings,
) -> pn.Column:
    """Render the sign-in page or the scene workspace for the request's session."""
    session = codec.from_cookies(cookies)
    if session is None:
        return _sign_in_view()
    return DashboardShell(store, client, session, settings).view


def create_panel_app(
    settings: DashboardSettings,
    codec: SessionCodec,
    store: StateStore,
    client: ReadClient,
) -> Callable[[], pn.Column]:
    """Build the Panel application callable served by FastAPI."""

    def app() -> pn.Column:
        return build_view(pn.state.cookies, codec, store, client, settings)

    return app
