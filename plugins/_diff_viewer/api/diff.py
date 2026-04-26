from __future__ import annotations

from helpers import files, projects, settings
from helpers.api import ApiHandler, Request, Response
from plugins._diff_viewer.helpers.diff import collect_workspace_diff


class Diff(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        context_id = str(input.get("context_id") or "").strip()
        workspace_path, display_path = self._resolve_workspace(context_id)
        return collect_workspace_diff(
            workspace_path,
            context_id=context_id,
            display_path=display_path,
        )

    def _resolve_workspace(self, context_id: str) -> tuple[str, str]:
        if context_id:
            context = self.use_context(context_id)
            project_name = projects.get_context_project_name(context)
            if project_name:
                project_path = projects.get_project_folder(project_name)
                display_path = files.normalize_a0_path(project_path)
                return files.fix_dev_path(display_path), display_path

        configured = str(settings.get_settings().get("workdir_path") or "")
        display_path = configured or files.normalize_a0_path(files.get_abs_path("usr/workdir"))
        return files.fix_dev_path(display_path), display_path
