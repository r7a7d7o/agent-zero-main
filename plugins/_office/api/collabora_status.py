from __future__ import annotations

from helpers.api import ApiHandler, Request
from plugins._office.helpers.collabora_status import collect_status, read_status


class CollaboraStatus(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        if input.get("fresh"):
            return collect_status()
        return read_status()
