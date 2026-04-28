from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import httpx
from helpers.api import ApiHandler, Request
from plugins._office.helpers import collabora_runtime, collabora_status, wopi_store


DISCOVERY_URLS = (
    "http://127.0.0.1:9980/office/hosting/discovery",
    "http://127.0.0.1:9980/hosting/discovery",
)


class OfficeSession(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        action = str(input.get("action") or "open").lower()
        if action == "status":
            return collabora_status.collect_status()
        if action == "retry":
            collabora_runtime.retry_bootstrap()
            return {"ok": True, **collabora_status.read_status()}
        if action == "recent":
            return {"ok": True, "documents": wopi_store.get_recent_documents()}
        if action == "open_documents":
            return {"ok": True, "documents": wopi_store.get_open_documents(limit=24)}
        if action == "sync_open_sessions":
            session_ids = input.get("session_ids")
            if not isinstance(session_ids, list):
                session_ids = []
            closed = wopi_store.sync_open_sessions(session_ids)
            return {"ok": True, "closed": closed, "documents": wopi_store.get_open_documents(limit=24)}
        if action == "close":
            closed = wopi_store.close_session(
                session_id=str(input.get("session_id") or ""),
                file_id=str(input.get("file_id") or ""),
            )
            return {"ok": True, "closed": closed, "documents": wopi_store.get_open_documents(limit=24)}
        if action == "create":
            doc = wopi_store.create_document(
                kind=str(input.get("kind") or "document"),
                title=str(input.get("title") or "Untitled"),
                fmt=str(input.get("format") or "docx"),
                content=str(input.get("content") or ""),
                path=str(input.get("path") or ""),
            )
            return await self._open_document(doc, input, request)
        if action == "open":
            file_id = str(input.get("file_id") or "").strip()
            doc = (
                wopi_store.get_document(file_id)
                if file_id
                else wopi_store.register_document(str(input.get("path") or ""))
            )
            return await self._open_document(doc, input, request)
        return {"ok": False, "error": f"Unsupported office session action: {action}"}

    async def _open_document(self, doc: dict, input: dict, request: Request) -> dict:
        mode = "edit" if str(input.get("mode") or "edit").lower() == "edit" else "view"
        permission = "write" if mode == "edit" else "read"
        origin = self._origin(request)
        session = wopi_store.create_session(
            doc["file_id"],
            user_id=str(input.get("user_id") or "agent-zero-user"),
            permission=permission,
            origin=origin,
        )
        discovery = await self._discover()
        if not discovery.get("ok"):
            return {
                "ok": False,
                "error": discovery.get("error") or "Collabora discovery is unavailable",
                "file_id": doc["file_id"],
                "title": doc["basename"],
                "extension": doc["extension"],
                "status": collabora_status.collect_status(),
            }

        action_url = self._select_action(discovery["xml"], doc["extension"], mode)
        if not action_url:
            return {
                "ok": False,
                "error": f"Collabora does not advertise {mode} support for .{doc['extension']}",
                "file_id": doc["file_id"],
                "title": doc["basename"],
                "extension": doc["extension"],
            }

        wopi_src = f"http://127.0.0.1:80/wopi/files/{doc['file_id']}"
        iframe_action = self._same_origin_action(action_url, wopi_src, session["session_id"])
        return {
            "ok": True,
            "file_id": doc["file_id"],
            "session_id": session["session_id"],
            "iframe_action": iframe_action,
            "access_token": session["access_token"],
            "access_token_ttl": session["access_token_ttl"],
            "post_message_origin": origin,
            "title": doc["basename"],
            "extension": doc["extension"],
            "path": doc["path"],
            "version": wopi_store.item_version(doc),
            "preview": wopi_store.build_preview(doc),
        }

    def _origin(self, request: Request) -> str:
        origin = request.headers.get("Origin") or request.host_url.rstrip("/")
        return origin.rstrip("/")

    async def _discover(self) -> dict:
        for url in DISCOVERY_URLS:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    response = await client.get(url)
                if response.status_code == 200 and "wopi-discovery" in response.text.lower():
                    return {"ok": True, "xml": response.text}
            except Exception:
                continue
        return {"ok": False, "error": "Collabora discovery is not reachable yet"}

    def _select_action(self, discovery_xml: str, extension: str, mode: str) -> str:
        root = ET.fromstring(discovery_xml)
        best = ""
        fallback = ""
        for action in root.findall(".//{*}action"):
            if action.attrib.get("ext", "").lower() != extension.lower():
                continue
            name = action.attrib.get("name", "").lower()
            urlsrc = action.attrib.get("urlsrc", "")
            if not urlsrc:
                continue
            if name == mode:
                best = urlsrc
                break
            if name == "view":
                fallback = urlsrc
        return best or fallback

    def _same_origin_action(self, urlsrc: str, wopi_src: str, session_id: str) -> str:
        parsed = urlparse(urlsrc)
        path = parsed.path or "/office/browser/cool.html"
        if not path.startswith("/office"):
            path = "/office" + path
        query = parsed.query
        base = path + (f"?{query}" if query else ("?" if urlsrc.endswith("?") else ""))
        separator = "" if base.endswith("?") or base.endswith("&") else ("&" if "?" in base else "?")
        base = f"{base}{separator}a0_session={quote(session_id, safe='')}"
        return f"{base}&WOPISrc={quote(wopi_src, safe='')}"
