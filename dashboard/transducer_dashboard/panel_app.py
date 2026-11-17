"""Panel frontend mounted inside the FastAPI dashboard service."""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping

import panel as pn

from .auth import Session, SessionCodec
from .settings import DashboardSettings


def _sign_in_view() -> pn.Column:
    return pn.Column(
        pn.pane.Markdown("# Transducer dashboard"),
        pn.pane.HTML(
            "<p>Sign in with a read API token to open the dashboard.</p>"
            '<p><a href="login">Sign in</a></p>'
        ),
    )


def _signed_in_view(session: Session) -> pn.Column:
    label = html.escape(session.label)
    return pn.Column(
        pn.pane.Markdown("# Transducer dashboard"),
        pn.pane.HTML(f"<p>Signed in as <strong>{label}</strong>.</p>"),
        pn.pane.HTML(
            "<p>The Panel workspace is being built. Scenes, figures, and settings "
            "will appear here.</p>"
            '<form method="post" action="logout"><button type="submit">Sign out</button></form>'
        ),
    )


def build_placeholder(
    cookies: Mapping[str, str], codec: SessionCodec
) -> pn.Column:
    """Render the mount-stage placeholder for the request's session."""
    session = codec.from_cookies(cookies)
    if session is None:
        return _sign_in_view()
    return _signed_in_view(session)


def create_panel_app(
    settings: DashboardSettings, codec: SessionCodec
) -> Callable[[], pn.Column]:
    """Build the Panel application callable served by FastAPI."""

    def app() -> pn.Column:
        return build_placeholder(pn.state.cookies, codec)

    return app
