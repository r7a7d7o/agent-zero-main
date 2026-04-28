from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from helpers import files


PLUGIN_NAME = "_office"
SUPPORTED_EXTENSIONS = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}
DEFAULT_TTL_SECONDS = 8 * 60 * 60
DEFAULT_LOCK_SECONDS = 30 * 60
ORPHAN_SESSION_GRACE_SECONDS = 30
MAX_LOCK_SECONDS = 3600
MIN_LOCK_SECONDS = 60
MAX_SAVE_BYTES = 512 * 1024 * 1024
PREVIEW_LINE_LIMIT = 5
PREVIEW_ROW_LIMIT = 5
PREVIEW_COLUMN_LIMIT = 4
PREVIEW_SLIDE_LIMIT = 2

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
X_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

STATE_DIR = Path(files.get_abs_path("usr", "plugins", PLUGIN_NAME, "collabora"))
DB_PATH = STATE_DIR / "documents.sqlite3"
BACKUP_DIR = STATE_DIR / "backups"
DOCUMENTS_DIR = Path(files.get_abs_path("usr", "workdir", "documents"))
WORKDIR = Path(files.get_abs_path("usr", "workdir"))


def now() -> float:
    return time.time()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_title(title: str, fallback: str = "Document") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " ._-" else "_" for ch in title).strip(" ._")
    return cleaned or fallback


def normalize_extension(value: str) -> str:
    ext = value.lower().strip().lstrip(".")
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported Office format: {ext}")
    return ext


def normalize_path(path: str | Path) -> Path:
    raw = str(path)
    if raw.startswith("/a0/") and not files.get_base_dir().startswith("/a0"):
        raw = files.get_abs_path(raw.removeprefix("/a0/"))
    candidate = Path(raw if os.path.isabs(raw) else files.get_abs_path(raw))
    resolved = candidate.expanduser().resolve(strict=False)
    allowed_roots = [WORKDIR.resolve(strict=False)]
    if not any(os.path.commonpath([str(resolved), str(root)]) == str(root) for root in allowed_roots):
        raise PermissionError("Office documents must be inside /a0/usr/workdir")
    if candidate.exists():
        real = candidate.resolve(strict=True)
        if not any(os.path.commonpath([str(real), str(root)]) == str(root) for root in allowed_roots):
            raise PermissionError("Office document symlink escapes the workdir")
    return resolved


@contextmanager
def connect() -> Any:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            file_id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            basename TEXT NOT NULL,
            extension TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            size INTEGER NOT NULL,
            version INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            last_modified TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            permission TEXT NOT NULL,
            origin TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token_hash TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            permission TEXT NOT NULL,
            source_path TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS locks (
            file_id TEXT PRIMARY KEY,
            lock_value TEXT NOT NULL,
            expires_at REAL NOT NULL,
            session_id TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            version TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )


def register_document(path: str | Path, owner_id: str = "a0") -> dict[str, Any]:
    resolved = normalize_path(path)
    if not resolved.exists():
        raise FileNotFoundError(str(resolved))
    ext = normalize_extension(resolved.suffix.lstrip("."))
    data = resolved.read_bytes()
    digest = sha256_bytes(data)
    stat = resolved.stat()
    current_time = now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE path = ?", (str(resolved),)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE documents
                SET basename=?, extension=?, size=?, sha256=?, last_modified=?, updated_at=?
                WHERE file_id=?
                """,
                (resolved.name, ext, stat.st_size, digest, now_iso(), current_time, row["file_id"]),
            )
            return get_document(row["file_id"], conn=conn)

        file_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO documents
            (file_id, path, basename, extension, owner_id, size, version, sha256, last_modified, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, str(resolved), resolved.name, ext, owner_id, stat.st_size, 1, digest, now_iso(), current_time, current_time),
        )
        _record_version(conn, file_id, resolved, "1", data)
        return get_document(file_id, conn=conn)


def get_document(file_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    def _fetch(active: sqlite3.Connection) -> dict[str, Any]:
        row = active.execute("SELECT * FROM documents WHERE file_id = ?", (file_id,)).fetchone()
        if not row:
            raise FileNotFoundError(file_id)
        return dict(row)

    if conn is not None:
        return _fetch(conn)
    with connect() as active:
        return _fetch(active)


def get_recent_documents(limit: int = 12, include_preview: bool = True) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        documents = [dict(row) for row in rows]
    if include_preview:
        return [with_preview(document) for document in documents]
    return documents


def get_open_documents(limit: int = 6) -> list[dict[str, Any]]:
    with connect() as conn:
        _clear_expired_sessions(conn)
        rows = conn.execute(
            """
            SELECT
                d.*,
                COUNT(s.session_id) AS open_sessions,
                MAX(s.created_at) AS last_opened_at,
                MAX(s.expires_at) AS session_expires_at
            FROM documents d
            JOIN sessions s ON s.file_id = d.file_id
            WHERE s.expires_at > ?
            GROUP BY d.file_id
            ORDER BY last_opened_at DESC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()
        return [dict(row) for row in rows]


def close_session(session_id: str = "", file_id: str = "") -> int:
    session_id = str(session_id or "").strip()
    file_id = str(file_id or "").strip()
    if not session_id and not file_id:
        return 0

    with connect() as conn:
        _clear_expired_sessions(conn)
        if session_id:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row:
                return 0
            conn.execute("DELETE FROM tokens WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM locks WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (row["file_id"], "close_session", json.dumps({"session_id": session_id}), now()),
            )
            return 1

        rows = conn.execute("SELECT session_id FROM sessions WHERE file_id = ?", (file_id,)).fetchall()
        conn.execute("DELETE FROM tokens WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM sessions WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM locks WHERE file_id = ?", (file_id,))
        conn.execute(
            "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (file_id, "close_document_sessions", json.dumps({"closed": len(rows)}), now()),
        )
        return len(rows)


def sync_open_sessions(active_session_ids: list[str] | tuple[str, ...] | set[str]) -> int:
    active_ids = {str(session_id).strip() for session_id in active_session_ids if str(session_id).strip()}
    with connect() as conn:
        _clear_expired_sessions(conn)
        cutoff = now() - ORPHAN_SESSION_GRACE_SECONDS
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            rows = conn.execute(
                f"SELECT session_id, file_id FROM sessions WHERE session_id NOT IN ({placeholders}) AND created_at < ?",
                (*tuple(active_ids), cutoff),
            ).fetchall()
        else:
            rows = conn.execute("SELECT session_id, file_id FROM sessions WHERE created_at < ?", (cutoff,)).fetchall()

        if not rows:
            return 0

        session_ids = tuple(row["session_id"] for row in rows)
        placeholders = ",".join("?" for _ in session_ids)
        conn.execute(f"DELETE FROM tokens WHERE session_id IN ({placeholders})", session_ids)
        conn.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholders})", session_ids)
        conn.execute(f"DELETE FROM locks WHERE session_id IN ({placeholders})", session_ids)

        for row in rows:
            conn.execute(
                "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    row["file_id"],
                    "close_orphan_session",
                    json.dumps({"session_id": row["session_id"]}),
                    now(),
                ),
            )
        return len(rows)


def create_session(file_id: str, user_id: str, permission: str, origin: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    permission = "write" if permission == "write" else "read"
    token = secrets.token_urlsafe(32)
    created = now()
    expires = created + ttl_seconds
    doc = get_document(file_id)
    session_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, file_id, user_id, permission, origin, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, file_id, user_id, permission, origin, created, expires),
        )
        conn.execute(
            """
            INSERT INTO tokens
            (token_hash, file_id, session_id, user_id, permission, source_path, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token_hash(token), file_id, session_id, user_id, permission, doc["path"], created, expires),
        )
    return {
        "session_id": session_id,
        "file_id": file_id,
        "access_token": token,
        "access_token_ttl": int(expires * 1000),
        "expires_at": expires,
        "permission": permission,
        "origin": origin,
    }


def with_preview(document: dict[str, Any]) -> dict[str, Any]:
    return {**document, "preview": build_preview(document)}


def build_preview(document: dict[str, Any]) -> dict[str, Any]:
    ext = str(document.get("extension") or "").lower()
    path = Path(str(document.get("path") or ""))
    preview = {
        "available": False,
        "kind": _preview_kind(ext),
        "lines": [],
        "rows": [],
        "slides": [],
    }
    if not path.exists():
        return preview
    try:
        if ext == "docx":
            lines = _preview_docx(path)
            return {**preview, "available": bool(lines), "lines": lines}
        if ext == "xlsx":
            rows = _preview_xlsx(path)
            return {**preview, "available": bool(rows), "rows": rows}
        if ext == "pptx":
            slides = _preview_pptx(path)
            return {**preview, "available": bool(slides), "slides": slides}
        if ext in {"odt", "ods", "odp"}:
            lines = _preview_odf(path)
            return {**preview, "available": bool(lines), "lines": lines}
    except Exception:
        return preview
    return preview


def _preview_kind(ext: str) -> str:
    if ext in {"xlsx", "ods"}:
        return "spreadsheet"
    if ext in {"pptx", "odp"}:
        return "presentation"
    if ext in {"docx", "odt"}:
        return "document"
    return "file"


def _qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def _clean_preview_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _preview_docx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    lines = []
    for paragraph in root.iter(_qn(W_NS, "p")):
        text = _clean_preview_text("".join(node.text or "" for node in paragraph.iter(_qn(W_NS, "t"))))
        if text:
            lines.append(text)
        if len(lines) >= PREVIEW_LINE_LIMIT:
            break
    return lines


def _preview_xlsx(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
            key=_natural_name_key,
        )
        if not sheet_names:
            return []
        root = ET.fromstring(archive.read(sheet_names[0]))

    rows = []
    for row in root.iter(_qn(X_NS, "row")):
        cells = []
        for cell in list(row)[:PREVIEW_COLUMN_LIMIT]:
            cells.append(_xlsx_cell_preview(cell, shared_strings))
        if any(cells):
            rows.append(cells)
        if len(rows) >= PREVIEW_ROW_LIMIT:
            break
    return rows


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings = []
    for item in root.iter(_qn(X_NS, "si")):
        strings.append(_clean_preview_text("".join(node.text or "" for node in item.iter(_qn(X_NS, "t")))))
    return strings


def _xlsx_cell_preview(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return _clean_preview_text("".join(node.text or "" for node in cell.iter(_qn(X_NS, "t"))))
    value_node = cell.find(_qn(X_NS, "v"))
    value = _clean_preview_text(value_node.text if value_node is not None else "")
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError):
            return value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _preview_pptx(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_natural_name_key,
        )
        slides = []
        for name in names[:PREVIEW_SLIDE_LIMIT]:
            root = ET.fromstring(archive.read(name))
            lines = []
            for paragraph in root.iter(_qn(A_NS, "p")):
                text = _clean_preview_text("".join(node.text or "" for node in paragraph.iter(_qn(A_NS, "t"))))
                if text:
                    lines.append(text)
            if lines:
                slides.append({"title": lines[0], "lines": lines[1:PREVIEW_LINE_LIMIT]})
        return slides


def _preview_odf(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    lines = []
    for node in root.iter():
        text = _clean_preview_text(node.text)
        if text:
            lines.append(text)
        if len(lines) >= PREVIEW_LINE_LIMIT:
            break
    return lines


def _natural_name_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def replace_document_bytes(
    file_id: str,
    data: bytes,
    actor: str = "agent",
    invalidate_sessions: bool = True,
) -> dict[str, Any]:
    if len(data) > MAX_SAVE_BYTES:
        raise OverflowError("Office save exceeds maximum size")
    with connect() as conn:
        doc = get_document(file_id, conn=conn)
        path = Path(doc["path"])
        previous = path.read_bytes() if path.exists() else b""
        if previous == data:
            return doc

        _record_version(conn, file_id, path, item_version(doc), previous)
        _write_atomic(path, data)
        digest = sha256_bytes(data)
        next_version = int(doc["version"]) + 1
        changed_at = now()
        conn.execute(
            """
            UPDATE documents
            SET size=?, version=?, sha256=?, last_modified=?, updated_at=?
            WHERE file_id=?
            """,
            (len(data), next_version, digest, now_iso(), changed_at, file_id),
        )
        if invalidate_sessions:
            conn.execute("DELETE FROM locks WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM tokens WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM sessions WHERE file_id = ?", (file_id,))
        conn.execute(
            "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (
                file_id,
                "direct_edit",
                json.dumps({"actor": actor, "version": f"{next_version}-{digest[:12]}"}),
                changed_at,
            ),
        )
        return get_document(file_id, conn=conn)


def validate_token(raw_token: str, file_id: str, require_write: bool = False) -> dict[str, Any]:
    if not raw_token:
        raise PermissionError("Missing WOPI access token")
    with connect() as conn:
        row = conn.execute("SELECT * FROM tokens WHERE token_hash = ?", (token_hash(raw_token),)).fetchone()
        if not row or row["file_id"] != file_id:
            raise PermissionError("Invalid WOPI access token")
        if row["expires_at"] < now():
            raise PermissionError("Expired WOPI access token")
        if require_write and row["permission"] != "write":
            raise PermissionError("WOPI token is read-only")
        session = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (row["session_id"],)).fetchone()
        return {"token": dict(row), "session": dict(session) if session else {}}


def check_file_info(file_id: str, token_info: dict[str, Any]) -> dict[str, Any]:
    doc = get_document(file_id)
    session = token_info.get("session") or {}
    can_write = (token_info.get("token") or {}).get("permission") == "write"
    origin = session.get("origin") or "http://localhost:32080"
    info = {
        "BaseFileName": doc["basename"],
        "OwnerId": doc["owner_id"],
        "Size": int(doc["size"]),
        "Version": item_version(doc),
        "UserId": session.get("user_id") or "agent-zero-user",
        "UserFriendlyName": "Agent Zero",
        "UserCanWrite": bool(can_write),
        "ReadOnly": not bool(can_write),
        "SupportsLocks": True,
        "SupportsUpdate": True,
        "SupportsExtendedLockLength": True,
        "SupportsGetLock": True,
        "UserCanNotWriteRelative": True,
        "PostMessageOrigin": origin,
        "ClosePostMessage": True,
        "CloseUrl": origin.rstrip("/") + "/",
        "LastModifiedTime": doc["last_modified"],
    }
    return {key: value for key, value in info.items() if value is not None}


def item_version(doc: dict[str, Any]) -> str:
    return f"{int(doc['version'])}-{str(doc['sha256'])[:12]}"


def get_lock(file_id: str) -> str:
    with connect() as conn:
        _clear_expired_locks(conn)
        row = conn.execute("SELECT lock_value FROM locks WHERE file_id = ?", (file_id,)).fetchone()
        return row["lock_value"] if row else ""


def lock(file_id: str, lock_value: str, session_id: str, timeout_seconds: int) -> tuple[bool, str]:
    timeout_seconds = clamp_lock_timeout(timeout_seconds)
    with connect() as conn:
        _clear_expired_locks(conn)
        row = conn.execute("SELECT * FROM locks WHERE file_id = ?", (file_id,)).fetchone()
        if row and row["lock_value"] != lock_value:
            return False, row["lock_value"]
        expires = now() + timeout_seconds
        conn.execute(
            """
            INSERT INTO locks (file_id, lock_value, expires_at, session_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET lock_value=excluded.lock_value, expires_at=excluded.expires_at, session_id=excluded.session_id, updated_at=excluded.updated_at
            """,
            (file_id, lock_value, expires, session_id, now()),
        )
        return True, lock_value


def refresh_lock(file_id: str, lock_value: str, timeout_seconds: int) -> tuple[bool, str]:
    timeout_seconds = clamp_lock_timeout(timeout_seconds)
    with connect() as conn:
        _clear_expired_locks(conn)
        row = conn.execute("SELECT * FROM locks WHERE file_id = ?", (file_id,)).fetchone()
        if not row or row["lock_value"] != lock_value:
            return False, row["lock_value"] if row else ""
        conn.execute(
            "UPDATE locks SET expires_at = ?, updated_at = ? WHERE file_id = ?",
            (now() + timeout_seconds, now(), file_id),
        )
        return True, lock_value


def unlock(file_id: str, lock_value: str) -> tuple[bool, str]:
    with connect() as conn:
        _clear_expired_locks(conn)
        row = conn.execute("SELECT * FROM locks WHERE file_id = ?", (file_id,)).fetchone()
        if not row:
            return True, ""
        if row["lock_value"] != lock_value:
            return False, row["lock_value"]
        conn.execute("DELETE FROM locks WHERE file_id = ?", (file_id,))
        return True, ""


def unlock_and_relock(file_id: str, old_lock: str, new_lock: str, session_id: str, timeout_seconds: int) -> tuple[bool, str]:
    with connect() as conn:
        _clear_expired_locks(conn)
        row = conn.execute("SELECT * FROM locks WHERE file_id = ?", (file_id,)).fetchone()
        if row and row["lock_value"] != old_lock:
            return False, row["lock_value"]
        expires = now() + clamp_lock_timeout(timeout_seconds)
        conn.execute(
            """
            INSERT INTO locks (file_id, lock_value, expires_at, session_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET lock_value=excluded.lock_value, expires_at=excluded.expires_at, session_id=excluded.session_id, updated_at=excluded.updated_at
            """,
            (file_id, new_lock, expires, session_id, now()),
        )
        return True, new_lock


def put_file(file_id: str, data: bytes, lock_value: str) -> str:
    if len(data) > MAX_SAVE_BYTES:
        raise OverflowError("Office save exceeds maximum size")
    with connect() as conn:
        _clear_expired_locks(conn)
        doc = get_document(file_id, conn=conn)
        current_lock = conn.execute("SELECT lock_value FROM locks WHERE file_id = ?", (file_id,)).fetchone()
        current = current_lock["lock_value"] if current_lock else ""
        path = Path(doc["path"])
        if current and current != lock_value:
            raise LockMismatch(current)
        if not current and int(doc["size"]) > 0:
            raise LockMismatch("")

        previous = path.read_bytes() if path.exists() else b""
        _record_version(conn, file_id, path, item_version(doc), previous)
        _write_atomic(path, data)
        digest = sha256_bytes(data)
        next_version = int(doc["version"]) + 1
        conn.execute(
            """
            UPDATE documents
            SET size=?, version=?, sha256=?, last_modified=?, updated_at=?
            WHERE file_id=?
            """,
            (len(data), next_version, digest, now_iso(), now(), file_id),
        )
        return f"{next_version}-{digest[:12]}"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


class LockMismatch(Exception):
    def __init__(self, current_lock: str) -> None:
        super().__init__("WOPI lock mismatch")
        self.current_lock = current_lock


def clamp_lock_timeout(value: int | str | None) -> int:
    try:
        seconds = int(value or DEFAULT_LOCK_SECONDS)
    except (TypeError, ValueError):
        seconds = DEFAULT_LOCK_SECONDS
    return max(MIN_LOCK_SECONDS, min(MAX_LOCK_SECONDS, seconds))


def _clear_expired_locks(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM locks WHERE expires_at < ?", (now(),))


def _clear_expired_sessions(conn: sqlite3.Connection) -> None:
    current = now()
    conn.execute("DELETE FROM tokens WHERE expires_at < ?", (current,))
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (current,))
    conn.execute("DELETE FROM locks WHERE expires_at < ?", (current,))


def _record_version(conn: sqlite3.Connection, file_id: str, path: Path, version: str, data: bytes) -> None:
    if not data:
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{file_id}-{int(time.time() * 1000)}-{version.replace('/', '_')}"
    backup_path.write_bytes(data)
    conn.execute(
        "INSERT INTO versions (file_id, version, path, size, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (file_id, version, str(backup_path), len(data), sha256_bytes(data), now()),
    )


def version_history(file_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, file_id, version, path, size, sha256, created_at FROM versions WHERE file_id = ? ORDER BY id DESC",
            (file_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def restore_version(file_id: str, version_id: int) -> dict[str, Any]:
    with connect() as conn:
        doc = get_document(file_id, conn=conn)
        row = conn.execute("SELECT * FROM versions WHERE id = ? AND file_id = ?", (version_id, file_id)).fetchone()
        if not row:
            raise FileNotFoundError(f"Version {version_id} not found")
        data = Path(row["path"]).read_bytes()
        path = Path(doc["path"])
        _record_version(conn, file_id, path, item_version(doc), path.read_bytes() if path.exists() else b"")
        path.write_bytes(data)
        digest = sha256_bytes(data)
        next_version = int(doc["version"]) + 1
        conn.execute(
            "UPDATE documents SET size=?, version=?, sha256=?, last_modified=?, updated_at=? WHERE file_id=?",
            (len(data), next_version, digest, now_iso(), now(), file_id),
        )
        return get_document(file_id, conn=conn)


def create_document(kind: str, title: str, fmt: str, content: str = "", path: str = "") -> dict[str, Any]:
    ext = normalize_extension(fmt)
    target = normalize_path(path) if path else _unique_document_path(title, ext)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(str(target))
    data = template_bytes(kind, ext, title, content)
    target.write_bytes(data)
    return register_document(target)


def _unique_document_path(title: str, ext: str) -> Path:
    base = safe_title(title, "Document")
    candidate = DOCUMENTS_DIR / f"{base}.{ext}"
    index = 2
    while candidate.exists():
        candidate = DOCUMENTS_DIR / f"{base} {index}.{ext}"
        index += 1
    return candidate.resolve(strict=False)


def template_bytes(kind: str, ext: str, title: str, content: str) -> bytes:
    if ext == "docx":
        return _docx(title, content)
    if ext == "xlsx":
        return _xlsx(title, content)
    if ext == "pptx":
        return _pptx(title, content)
    if ext in {"odt", "ods", "odp"}:
        return _odf(ext, title, content)
    raise ValueError(ext)


def _zip_bytes(files_map: dict[str, str | bytes], stored: set[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files_map.items():
            data = value.encode("utf-8") if isinstance(value, str) else value
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if stored and name in stored else zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def _docx(title: str, content: str) -> bytes:
    lines = [title] + [line for line in content.splitlines() if line.strip()]
    body = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in lines)
    return _zip_bytes({
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""",
        "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr/></w:body></w:document>""",
    })


def _xlsx(title: str, content: str) -> bytes:
    rows = _xlsx_rows(title, content)
    sheet_rows = "".join(
        f'<row r="{row_idx}">{"".join(_xlsx_cell(row_idx, col_idx, value) for col_idx, value in enumerate(row, start=1))}</row>'
        for row_idx, row in enumerate(rows, start=1)
    )
    return _zip_bytes({
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet_rows}</sheetData></worksheet>""",
    })


def _xlsx_rows(title: str, content: str) -> list[list[Any]]:
    parsed = _tabular_rows(content)
    if parsed:
        return parsed
    lines = [line for line in str(content or "").splitlines() if line.strip()]
    if lines:
        return [[title], *[[line] for line in lines]]
    return [[title]]


def _tabular_rows(content: str) -> list[list[Any]]:
    text = str(content or "").strip("\n")
    if not text.strip():
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    markdown_rows = _markdown_table_rows(lines)
    if markdown_rows:
        return markdown_rows

    delimiter = "\t" if any("\t" in line for line in lines) else ("," if any("," in line for line in lines) else None)
    if not delimiter:
        return []
    return [[_xlsx_value(cell) for cell in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)]


def _markdown_table_rows(lines: list[str]) -> list[list[Any]]:
    table_lines = [line.strip() for line in lines if line.strip().startswith("|") and line.strip().endswith("|")]
    if len(table_lines) < 2:
        return []
    rows = []
    for line in table_lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            continue
        rows.append([_xlsx_value(cell) for cell in cells])
    return rows


def _xlsx_cell(row_idx: int, col_idx: int, value: Any) -> str:
    ref = f"{_column_name(col_idx)}{row_idx}"
    value = _xlsx_value(value)
    if value in (None, ""):
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def _xlsx_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped.lower() in {"true", "false"}:
        return stripped.lower() == "true"
    if re.fullmatch(r"[+-]?\d+", stripped) and not (len(stripped.lstrip("+-")) > 1 and stripped.lstrip("+-").startswith("0")):
        try:
            return int(stripped)
        except ValueError:
            return stripped
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", stripped) or re.fullmatch(r"[+-]?\d+[eE][+-]?\d+", stripped):
        try:
            return float(stripped)
        except ValueError:
            return stripped
    return stripped


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _pptx(title: str, content: str) -> bytes:
    subtitle = content.splitlines()[0] if content.splitlines() else ""
    return _zip_bytes({
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>""",
        "ppt/_rels/presentation.xml.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>""",
        "ppt/presentation.xml": """<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="5143500"/></p:presentation>""",
        "ppt/slides/slide1.xml": f"""<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p><a:p><a:r><a:t>{escape(subtitle)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>""",
    })


def _odf(ext: str, title: str, content: str) -> bytes:
    mime = {
        "odt": "application/vnd.oasis.opendocument.text",
        "ods": "application/vnd.oasis.opendocument.spreadsheet",
        "odp": "application/vnd.oasis.opendocument.presentation",
    }[ext]
    body = f"<text:p>{escape(title)}</text:p><text:p>{escape(content)}</text:p>"
    return _zip_bytes({
        "mimetype": mime,
        "META-INF/manifest.xml": f"""<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry manifest:media-type="{mime}" manifest:full-path="/"/><manifest:file-entry manifest:media-type="text/xml" manifest:full-path="content.xml"/></manifest:manifest>""",
        "content.xml": f"""<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2"><office:body><office:text>{body}</office:text></office:body></office:document-content>""",
    }, stored={"mimetype"})
