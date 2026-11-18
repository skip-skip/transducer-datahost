"""Fast Panel-only development server.

Run from the repository root:

    python -m panel serve dashboard/dev_panel.py --dev --index dev_panel --port 8081

``--index dev_panel`` serves the app at ``/``; without it Panel mounts it at
``/dev_panel``. This serves the workspace with fake data and no authentication. Use
``scripts/dev_dashboard.ps1 -Mode prod`` for the FastAPI/auth/CSP path.
"""

from __future__ import annotations

import panel as pn

from dev_fixtures import build_shell

pn.extension("gridstack")

# The shell header carries a logout form for the FastAPI app. There is no
# logout route in this mode, so hide the button.
_HIDE_LOGOUT_CSS = ".td-header form { display: none !important; }"
if _HIDE_LOGOUT_CSS not in pn.config.raw_css:
    pn.config.raw_css.append(_HIDE_LOGOUT_CSS)

build_shell().view.servable()
