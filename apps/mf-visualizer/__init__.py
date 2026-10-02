"""mf-visualizer agent half: intentionally inert.

All the machinery lives in the dashboard API (dashboard/plugin_api.py, mounted
by the web server) and the desktop pane (desktop/plugin.js, materialized into
<HERMES_HOME>/desktop-plugins/ by the desktop app). The agent loader only needs
a register(ctx) to be present so the package discovers cleanly.
"""


def register(ctx) -> None:  # noqa: ARG001 - context unused; no tools/hooks here
    return None
