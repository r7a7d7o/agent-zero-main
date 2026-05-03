from __future__ import annotations

from helpers.extension import Extension
from helpers.print_style import PrintStyle
from plugins._office import hooks
from plugins._office.helpers import libreoffice_desktop_routes


class OfficeStartupCleanup(Extension):
    def execute(self, **kwargs):
        libreoffice_desktop_routes.install_route_hooks()
        result = hooks.cleanup_stale_runtime_state()
        if result.get("errors"):
            PrintStyle.warning("Office runtime preparation reported errors:", result["errors"])
        elif result.get("installed") or result.get("removed"):
            PrintStyle.info("Office runtime prepared:", result)
