from __future__ import annotations

from helpers.extension import Extension
from plugins._office.helpers.collabora_runtime import start_bootstrap_worker


class CollaboraBootstrap(Extension):
    def execute(self, **kwargs):
        start_bootstrap_worker(force=False)
