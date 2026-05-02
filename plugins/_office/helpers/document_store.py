from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
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
SUPPORTED_EXTENSIONS = {"md", "docx", "xlsx", "pptx"}
DEFAULT_TTL_SECONDS = 8 * 60 * 60
ORPHAN_SESSION_GRACE_SECONDS = 30
MAX_SAVE_BYTES = 512 * 1024 * 1024
PREVIEW_LINE_LIMIT = 5
PREVIEW_ROW_LIMIT = 5
PREVIEW_COLUMN_LIMIT = 4
PREVIEW_SLIDE_LIMIT = 2

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
X_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

STATE_DIR = Path(files.get_abs_path("usr", "plugins", PLUGIN_NAME, "documents"))
DB_PATH = STATE_DIR / "documents.sqlite3"
BACKUP_DIR = STATE_DIR / "backups"
WORKDIR = Path(files.get_abs_path("usr", "workdir"))
DOCUMENTS_DIR = WORKDIR / "documents"


def now() -> float:
    return time.time()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_title(title: str, fallback: str = "Document") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " ._-" else "_" for ch in title).strip(" ._")
    return cleaned or fallback


def normalize_extension(value: str) -> str:
    ext = value.lower().strip().lstrip(".")
    if not ext:
        ext = "md"
    if ext not in SUPPORTED_EXTENSIONS:
        if ext == "odt":
            raise ValueError("ODT editing is not supported in this migration. Use Markdown or DOCX.")
        raise ValueError(f"Unsupported document format: {ext}")
    return ext


def document_home(context_id: str = "") -> Path:
    context_id = str(context_id or "").strip()
    if context_id:
        try:
            from agent import AgentContext

            context = AgentContext.get(context_id)
            project_helpers = _projects()
            project_name = project_helpers.get_context_project_name(context) if context else None
            if project_name:
                return Path(project_helpers.get_project_folder(project_name)).resolve(strict=False)
        except Exception:
            pass

    configured = str(_settings().get_settings().get("workdir_path") or "").strip()
    if configured:
        return _path_from_a0(configured).resolve(strict=False)
    return WORKDIR.resolve(strict=False)


def document_binary_home(context_id: str = "") -> Path:
    if str(context_id or "").strip():
        return document_home(context_id) / "documents"
    return DOCUMENTS_DIR.resolve(strict=False)


def default_open_path(context_id: str = "") -> str:
    return display_path(document_home(context_id))


def display_path(path: str | Path) -> str:
    resolved = Path(path).resolve(strict=False)
    base = Path(files.get_base_dir()).resolve(strict=False)
    if str(base).startswith("/a0"):
        return str(resolved)
    try:
        return "/a0/" + str(resolved.relative_to(base)).lstrip("/")
    except ValueError:
        return str(path)


def _path_from_a0(path: str | Path) -> Path:
    raw = str(path)
    if raw.startswith("/a0/") and not files.get_base_dir().startswith("/a0"):
        raw = files.get_abs_path(raw.removeprefix("/a0/"))
    return Path(raw if os.path.isabs(raw) else files.get_abs_path(raw)).expanduser()


def allowed_roots(context_id: str = "") -> list[Path]:
    project_helpers = _projects()
    roots = {
        WORKDIR.resolve(strict=False),
        DOCUMENTS_DIR.resolve(strict=False),
        Path(project_helpers.get_projects_parent_folder()).resolve(strict=False),
        document_home(context_id).resolve(strict=False),
        document_binary_home(context_id).resolve(strict=False),
    }
    configured = str(_settings().get_settings().get("workdir_path") or "").strip()
    if configured:
        roots.add(_path_from_a0(configured).resolve(strict=False))
    return sorted(roots, key=lambda item: str(item))


def _projects() -> Any:
    from helpers import projects

    return projects


def _settings() -> Any:
    from helpers import settings

    return settings


def normalize_path(path: str | Path, context_id: str = "") -> Path:
    candidate = _path_from_a0(path)
    resolved = candidate.resolve(strict=False)
    roots = allowed_roots(context_id)
    if not any(_is_relative_to(resolved, root) for root in roots):
        raise PermissionError("Document artifacts must stay inside the active project or workdir.")
    if candidate.exists():
        real = candidate.resolve(strict=True)
        if not any(_is_relative_to(real, root) for root in roots):
            raise PermissionError("Document artifact symlink escapes the active project or workdir.")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        os.path.commonpath([str(path), str(root)])
    except ValueError:
        return False
    return os.path.commonpath([str(path), str(root)]) == str(root)


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


def register_document(path: str | Path, owner_id: str = "a0", context_id: str = "") -> dict[str, Any]:
    resolved = normalize_path(path, context_id=context_id)
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
        return [with_preview(dict(row)) for row in rows]


def create_session(
    file_id: str,
    user_id: str = "agent-zero-user",
    permission: str = "write",
    origin: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    permission = "write" if permission == "write" else "read"
    created = now()
    expires = created + ttl_seconds
    session_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, file_id, user_id, permission, origin, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, file_id, user_id, permission, origin, created, expires),
        )
    return {
        "session_id": session_id,
        "file_id": file_id,
        "expires_at": expires,
        "permission": permission,
        "origin": origin,
    }


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
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (row["file_id"], "close_session", json.dumps({"session_id": session_id}), now()),
            )
            return 1

        rows = conn.execute("SELECT session_id FROM sessions WHERE file_id = ?", (file_id,)).fetchall()
        conn.execute("DELETE FROM sessions WHERE file_id = ?", (file_id,))
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
        conn.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholders})", session_ids)
        for row in rows:
            conn.execute(
                "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (row["file_id"], "close_orphan_session", json.dumps({"session_id": row["session_id"]}), now()),
            )
        return len(rows)


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
        if ext == "md":
            lines = _preview_markdown(path)
            return {**preview, "available": bool(lines), "lines": lines}
        if ext == "docx":
            lines = _preview_docx(path)
            return {**preview, "available": bool(lines), "lines": lines}
        if ext == "xlsx":
            rows = _preview_xlsx(path)
            return {**preview, "available": bool(rows), "rows": rows}
        if ext == "pptx":
            slides = _preview_pptx(path)
            return {**preview, "available": bool(slides), "slides": slides}
    except Exception:
        return preview
    return preview


def _preview_kind(ext: str) -> str:
    if ext == "xlsx":
        return "spreadsheet"
    if ext == "pptx":
        return "presentation"
    if ext in {"md", "docx"}:
        return "document"
    return "file"


def _qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def _clean_preview_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _preview_markdown(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = _clean_preview_text(raw.lstrip("#>-*0123456789.[]() "))
        if text:
            lines.append(text)
        if len(lines) >= PREVIEW_LINE_LIMIT:
            break
    return lines


def _preview_docx(path: Path) -> list[str]:
    return _docx_paragraphs(path, limit=PREVIEW_LINE_LIMIT)


def _docx_paragraphs(path: Path, limit: int | None = None) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    lines = []
    for paragraph in root.iter(_qn(W_NS, "p")):
        text = _clean_preview_text("".join(node.text or "" for node in paragraph.iter(_qn(W_NS, "t"))))
        if text:
            lines.append(text)
        if limit is not None and len(lines) >= limit:
            break
    return lines


def read_text_for_editor(doc: dict[str, Any]) -> str:
    path = Path(doc["path"])
    ext = str(doc["extension"]).lower()
    if ext == "md":
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == "docx":
        return "\n\n".join(_docx_paragraphs(path))
    raise ValueError(f"Text editing is not available for .{ext}.")


def write_markdown(file_id: str, content: str) -> dict[str, Any]:
    return replace_document_bytes(file_id, str(content or "").encode("utf-8"), actor="office:markdown")


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


def _natural_name_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def replace_document_bytes(
    file_id: str,
    data: bytes,
    actor: str = "agent",
    invalidate_sessions: bool = False,
) -> dict[str, Any]:
    if len(data) > MAX_SAVE_BYTES:
        raise OverflowError("Document save exceeds maximum size")
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
            conn.execute("DELETE FROM sessions WHERE file_id = ?", (file_id,))
        conn.execute(
            "INSERT INTO events (file_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (file_id, "saved", json.dumps({"actor": actor, "version": f"{next_version}-{digest[:12]}"}), changed_at),
        )
        return get_document(file_id, conn=conn)


def item_version(doc: dict[str, Any]) -> str:
    return f"{int(doc['version'])}-{str(doc['sha256'])[:12]}"


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


def _clear_expired_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))


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
        _write_atomic(path, data)
        digest = sha256_bytes(data)
        next_version = int(doc["version"]) + 1
        conn.execute(
            "UPDATE documents SET size=?, version=?, sha256=?, last_modified=?, updated_at=? WHERE file_id=?",
            (len(data), next_version, digest, now_iso(), now(), file_id),
        )
        return get_document(file_id, conn=conn)


def create_document(
    kind: str,
    title: str,
    fmt: str = "md",
    content: str = "",
    path: str = "",
    context_id: str = "",
) -> dict[str, Any]:
    ext = normalize_extension(fmt or "md")
    target = normalize_path(path, context_id=context_id) if path else _unique_document_path(title, ext, context_id=context_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(str(target))
    data = template_bytes(kind, ext, title, content)
    _write_atomic(target, data)
    return register_document(target, context_id=context_id)


def _unique_document_path(title: str, ext: str, context_id: str = "") -> Path:
    base = safe_title(title, "Document")
    root = document_home(context_id) if ext == "md" else document_binary_home(context_id)
    candidate = root / f"{base}.{ext}"
    index = 2
    while candidate.exists():
        candidate = root / f"{base} {index}.{ext}"
        index += 1
    return candidate.resolve(strict=False)


def template_bytes(kind: str, ext: str, title: str, content: str) -> bytes:
    ext = normalize_extension(ext or "md")
    if ext == "md":
        return _markdown(title, content).encode("utf-8")
    if ext == "docx":
        return _docx(title, content)
    if ext == "xlsx":
        return _xlsx(title, content)
    if ext == "pptx":
        return _pptx(title, content)
    raise ValueError(ext)


def _markdown(title: str, content: str) -> str:
    text = str(content or "").strip()
    if text:
        return text if text.startswith("#") else f"# {title}\n\n{text}\n"
    return f"# {title}\n"


def _zip_bytes(files_map: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files_map.items():
            data = value.encode("utf-8") if isinstance(value, str) else value
            archive.writestr(name, data)
    return buffer.getvalue()


def _docx(title: str, content: str) -> bytes:
    lines = [title] + [line for line in content.splitlines() if line.strip()]
    if len(lines) == 1:
        lines.append("")
    body = "".join(_docx_paragraph(line) for line in lines)
    return _zip_bytes({
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""",
        "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr/></w:body></w:document>""",
    })


def _docx_paragraph(line: str) -> str:
    if not str(line).strip():
        return '<w:p><w:r><w:t xml:space="preserve">&#160;</w:t></w:r></w:p>'
    return f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>"


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
