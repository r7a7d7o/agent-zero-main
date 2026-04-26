from __future__ import annotations

from helpers.extension import Extension
from plugins._office.helpers.collabora_runtime import reconcile


class CollaboraReconcile(Extension):
    async def execute(self, **kwargs):
        reconcile()
