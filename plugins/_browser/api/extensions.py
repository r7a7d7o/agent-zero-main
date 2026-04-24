from helpers.api import ApiHandler, Request
from plugins._browser.helpers.extension_manager import (
    get_extensions_root,
    install_chrome_web_store_extension,
    list_browser_extensions,
)


class Extensions(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        action = input.get("action", "list")

        if action == "list":
            return {
                "ok": True,
                "root": str(get_extensions_root()),
                "extensions": list_browser_extensions(),
            }

        if action == "install_web_store":
            try:
                result = install_chrome_web_store_extension(str(input.get("url", "")))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return result

        return {"ok": False, "error": f"Unknown action: {action}"}
