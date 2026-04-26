from __future__ import annotations

from helpers.extension import Extension
from plugins._office.helpers.route_bootstrap import install_route_hooks


class OfficeRoutesStartup(Extension):
    def execute(self, **kwargs):
        install_route_hooks()
