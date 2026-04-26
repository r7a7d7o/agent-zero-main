from __future__ import annotations

from helpers.api import ApiHandler, Request
from plugins._office.helpers import collabora_status


class CollaboraLogs(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        return {
            "ok": True,
            "bootstrap": collabora_status.tail_file(collabora_status.BOOTSTRAP_LOG),
            "wrapper": collabora_status.tail_file(collabora_status.WRAPPER_LOG),
        }
