from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from flask import Flask, Response, request, send_file

from plugins._office.helpers import wopi_store


def register_wopi_routes(app: Flask) -> None:
    if getattr(app, "_a0_office_wopi_routes_registered", False):
        return
    app._a0_office_wopi_routes_registered = True

    app.add_url_rule("/wopi/files/<file_id>", "office_wopi_file", wopi_file, methods=["GET", "POST"])
    app.add_url_rule("/wopi/files/<file_id>/contents", "office_wopi_contents", wopi_contents, methods=["GET", "POST"])


def token_from_request() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.args.get("access_token", "") or request.form.get("access_token", "")


def validate(file_id: str, require_write: bool = False) -> dict[str, Any] | Response:
    try:
        return wopi_store.validate_token(token_from_request(), file_id, require_write=require_write)
    except PermissionError as exc:
        return Response(str(exc), status=401)
    except Exception as exc:
        return Response(str(exc), status=404)


def json_response(data: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False),
        status=status,
        mimetype="application/json",
        headers=headers or {},
    )


def conflict(current_lock: str, reason: str = "Lock mismatch") -> Response:
    return Response(
        "",
        status=409,
        headers={
            "X-WOPI-Lock": current_lock or "",
            "X-WOPI-LockFailureReason": reason,
        },
    )


def wopi_file(file_id: str):
    if request.method == "GET":
        token_info = validate(file_id)
        if isinstance(token_info, Response):
            return token_info
        try:
            return json_response(wopi_store.check_file_info(file_id, token_info))
        except FileNotFoundError:
            return Response("File not found", status=404)
        except Exception as exc:
            return Response(str(exc), status=500)

    override = request.headers.get("X-WOPI-Override", "").upper().replace("-", "_")
    require_write = override in {"LOCK", "REFRESH_LOCK", "UNLOCK"}
    token_info = validate(file_id, require_write=require_write)
    if isinstance(token_info, Response):
        return token_info

    lock_value = request.headers.get("X-WOPI-Lock", "")
    old_lock = request.headers.get("X-WOPI-OldLock", "")
    timeout = request.headers.get("X-WOPI-LockExpirationTimeout")
    session_id = (token_info.get("token") or {}).get("session_id", "")

    try:
        if override == "GET_LOCK":
            return Response("", status=200, headers={"X-WOPI-Lock": wopi_store.get_lock(file_id)})
        if override == "LOCK" and old_lock:
            ok, current = wopi_store.unlock_and_relock(file_id, old_lock, lock_value, session_id, timeout)
            return Response("", status=200) if ok else conflict(current)
        if override == "LOCK":
            ok, current = wopi_store.lock(file_id, lock_value, session_id, timeout)
            return Response("", status=200) if ok else conflict(current)
        if override == "REFRESH_LOCK":
            ok, current = wopi_store.refresh_lock(file_id, lock_value, timeout)
            return Response("", status=200) if ok else conflict(current)
        if override == "UNLOCK":
            ok, current = wopi_store.unlock(file_id, lock_value)
            return Response("", status=200) if ok else conflict(current)
        return Response("Unsupported WOPI override", status=501)
    except Exception as exc:
        return Response(str(exc), status=500)


def wopi_contents(file_id: str):
    if request.method == "GET":
        token_info = validate(file_id)
        if isinstance(token_info, Response):
            return token_info
        try:
            doc = wopi_store.get_document(file_id)
            path = Path(doc["path"])
            mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return send_file(path, mimetype=mimetype, as_attachment=False, download_name=doc["basename"])
        except FileNotFoundError:
            return Response("File not found", status=404)
        except Exception as exc:
            return Response(str(exc), status=500)

    override = request.headers.get("X-WOPI-Override", "").upper().replace("-", "_")
    if override != "PUT":
        return Response("Unsupported WOPI override", status=501)
    token_info = validate(file_id, require_write=True)
    if isinstance(token_info, Response):
        return token_info

    try:
        version = wopi_store.put_file(file_id, request.get_data() or b"", request.headers.get("X-WOPI-Lock", ""))
        return Response("", status=200, headers={"X-WOPI-ItemVersion": version})
    except wopi_store.LockMismatch as exc:
        return conflict(exc.current_lock)
    except OverflowError as exc:
        return Response(str(exc), status=413)
    except FileNotFoundError:
        return Response("File not found", status=404)
    except Exception as exc:
        return Response(str(exc), status=500)
