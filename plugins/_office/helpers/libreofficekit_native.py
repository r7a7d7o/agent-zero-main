from __future__ import annotations

import ctypes
import atexit
import base64
import math
import json
import os
import shutil
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any


PROGRAM_DIR = Path(os.environ.get("A0_LIBREOFFICE_PROGRAM_DIR") or "/usr/lib/libreoffice/program")
MERGED_LIBRARY = PROGRAM_DIR / "libmergedlo.so"
DEFAULT_TILE_WIDTH_PX = 920
MAX_TILE_HEIGHT_PX = 1800
MAX_TILES = 12


class LibreOfficeKitNativeError(RuntimeError):
    pass


class _Office(ctypes.Structure):
    pass


class _OfficeClass(ctypes.Structure):
    pass


class _Document(ctypes.Structure):
    pass


class _DocumentClass(ctypes.Structure):
    pass


_OfficePtr = ctypes.POINTER(_Office)
_DocumentPtr = ctypes.POINTER(_Document)

_DestroyOffice = ctypes.CFUNCTYPE(None, _OfficePtr)
_DocumentLoad = ctypes.CFUNCTYPE(_DocumentPtr, _OfficePtr, ctypes.c_char_p)
_GetError = ctypes.CFUNCTYPE(ctypes.c_char_p, _OfficePtr)
_DocumentLoadWithOptions = ctypes.CFUNCTYPE(_DocumentPtr, _OfficePtr, ctypes.c_char_p, ctypes.c_char_p)
_FreeError = ctypes.CFUNCTYPE(None, ctypes.c_char_p)

_DestroyDocument = ctypes.CFUNCTYPE(None, _DocumentPtr)
_SaveAs = ctypes.CFUNCTYPE(ctypes.c_int, _DocumentPtr, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p)
_GetDocumentType = ctypes.CFUNCTYPE(ctypes.c_int, _DocumentPtr)
_GetParts = ctypes.CFUNCTYPE(ctypes.c_int, _DocumentPtr)
_GetPartPageRectangles = ctypes.CFUNCTYPE(ctypes.c_char_p, _DocumentPtr)
_GetPart = ctypes.CFUNCTYPE(ctypes.c_int, _DocumentPtr)
_SetPart = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_int)
_GetPartName = ctypes.CFUNCTYPE(ctypes.c_char_p, _DocumentPtr, ctypes.c_int)
_SetPartMode = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_int)
_PaintTile = ctypes.CFUNCTYPE(
    None,
    _DocumentPtr,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
)
_GetTileMode = ctypes.CFUNCTYPE(ctypes.c_int, _DocumentPtr)
_GetDocumentSize = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long))
_InitializeForRendering = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_char_p)
_RegisterDocumentCallback = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_void_p, ctypes.c_void_p)
_PostKeyEvent = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_PostMouseEvent = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_PostUnoCommand = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_bool)
_SetTextSelection = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_GetTextSelection = ctypes.CFUNCTYPE(ctypes.c_char_p, _DocumentPtr, ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p))
_Paste = ctypes.CFUNCTYPE(ctypes.c_bool, _DocumentPtr, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t)
_SetGraphicSelection = ctypes.CFUNCTYPE(None, _DocumentPtr, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_ResetSelection = ctypes.CFUNCTYPE(None, _DocumentPtr)
_GetCommandValues = ctypes.CFUNCTYPE(ctypes.c_char_p, _DocumentPtr, ctypes.c_char_p)


_Office._fields_ = [("pClass", ctypes.POINTER(_OfficeClass))]
_OfficeClass._fields_ = [
    ("nSize", ctypes.c_size_t),
    ("destroy", _DestroyOffice),
    ("documentLoad", _DocumentLoad),
    ("getError", _GetError),
    ("documentLoadWithOptions", _DocumentLoadWithOptions),
    ("freeError", _FreeError),
]

_Document._fields_ = [("pClass", ctypes.POINTER(_DocumentClass))]
_DocumentClass._fields_ = [
    ("nSize", ctypes.c_size_t),
    ("destroy", _DestroyDocument),
    ("saveAs", _SaveAs),
    ("getDocumentType", _GetDocumentType),
    ("getParts", _GetParts),
    ("getPartPageRectangles", _GetPartPageRectangles),
    ("getPart", _GetPart),
    ("setPart", _SetPart),
    ("getPartName", _GetPartName),
    ("setPartMode", _SetPartMode),
    ("paintTile", _PaintTile),
    ("getTileMode", _GetTileMode),
    ("getDocumentSize", _GetDocumentSize),
    ("initializeForRendering", _InitializeForRendering),
    ("registerCallback", _RegisterDocumentCallback),
    ("postKeyEvent", _PostKeyEvent),
    ("postMouseEvent", _PostMouseEvent),
    ("postUnoCommand", _PostUnoCommand),
    ("setTextSelection", _SetTextSelection),
    ("getTextSelection", _GetTextSelection),
    ("paste", _Paste),
    ("setGraphicSelection", _SetGraphicSelection),
    ("resetSelection", _ResetSelection),
    ("getCommandValues", _GetCommandValues),
]


def available() -> bool:
    return PROGRAM_DIR.exists() and MERGED_LIBRARY.exists() and os.environ.get("A0_OFFICE_DISABLE_NATIVE_LOK") != "1"


def open_document(path: str | Path) -> Any:
    from plugins._office.helpers import libreofficekit_worker

    return libreofficekit_worker.open_document(path)


def open_document_in_process(path: str | Path) -> "NativeLokDocument":
    return get_office().open_document(path)


def get_office() -> "NativeLokOffice":
    global _office
    try:
        return _office
    except NameError:
        _office = NativeLokOffice()
        atexit.register(_close_global_office)
        return _office


def _close_global_office() -> None:
    office = globals().get("_office")
    if office:
        try:
            office.close()
        except Exception:
            pass


class NativeLokOffice:
    def __init__(self) -> None:
        if not available():
            raise LibreOfficeKitNativeError("LibreOfficeKit native library is not available.")

        os.environ.setdefault("HOME", "/tmp")
        os.environ.setdefault("SAL_USE_VCLPLUGIN", "gen")
        self._profile_dir = Path(tempfile.mkdtemp(prefix="a0-lok-profile-"))
        self._library = ctypes.CDLL(str(MERGED_LIBRARY))
        self._library.libreofficekit_hook_2.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self._library.libreofficekit_hook_2.restype = _OfficePtr
        profile_url = f"file://{self._profile_dir}".encode("utf-8")
        self._office = self._library.libreofficekit_hook_2(str(PROGRAM_DIR).encode("utf-8"), profile_url)
        if not self._office:
            raise LibreOfficeKitNativeError("LibreOfficeKit hook returned no office instance.")

    def open_document(self, path: str | Path) -> "NativeLokDocument":
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(str(source))
        loaded = self._office.contents.pClass.contents.documentLoad(self._office, str(source).encode("utf-8"))
        if not loaded:
            raise LibreOfficeKitNativeError(self.error() or f"LibreOfficeKit could not load {source}.")
        document = NativeLokDocument(loaded, source)
        document.initialize_for_rendering()
        return document

    def error(self) -> str:
        get_error = self._office.contents.pClass.contents.getError
        if not get_error:
            return ""
        value = get_error(self._office)
        return _decode_c_string(value)

    def close(self) -> None:
        office = getattr(self, "_office", None)
        if office:
            office.contents.pClass.contents.destroy(office)
            self._office = None


class NativeLokDocument:
    def __init__(self, document: _DocumentPtr, path: Path) -> None:
        self._document = document
        self.path = path

    @property
    def _class(self) -> _DocumentClass:
        return self._document.contents.pClass.contents

    def initialize_for_rendering(self) -> None:
        self._class.initializeForRendering(self._document, None)

    def metadata(self) -> dict[str, Any]:
        width = ctypes.c_long()
        height = ctypes.c_long()
        self._class.getDocumentSize(self._document, ctypes.byref(width), ctypes.byref(height))
        page_rectangles = self.page_rectangles(width.value, height.value)
        return {
            "available": True,
            "doctype": self._class.getDocumentType(self._document),
            "parts": self._class.getParts(self._document),
            "part": self._class.getPart(self._document),
            "tile_mode": self._class.getTileMode(self._document),
            "width_twips": int(width.value),
            "height_twips": int(height.value),
            "page_rectangles": page_rectangles,
        }

    def page_rectangles(self, width: int = 0, height: int = 0) -> list[dict[str, int]]:
        raw = self._class.getPartPageRectangles(self._document)
        rectangles = _parse_rectangles(_decode_c_string(raw))
        if rectangles:
            return rectangles
        if not width or not height:
            width_ref = ctypes.c_long()
            height_ref = ctypes.c_long()
            self._class.getDocumentSize(self._document, ctypes.byref(width_ref), ctypes.byref(height_ref))
            width = int(width_ref.value)
            height = int(height_ref.value)
        return [{"x": 0, "y": 0, "width": int(width), "height": int(height)}]

    def render_tiles(self, pixel_width: int = DEFAULT_TILE_WIDTH_PX, max_tiles: int = MAX_TILES) -> list[dict[str, Any]]:
        tile_mode = int(self._class.getTileMode(self._document))
        tiles: list[dict[str, Any]] = []
        for index, rectangle in enumerate(self.page_rectangles()[:max_tiles]):
            width_twips = max(1, int(rectangle["width"]))
            height_twips = max(1, int(rectangle["height"]))
            width_px = max(320, min(int(pixel_width), 1400))
            height_px = max(320, min(MAX_TILE_HEIGHT_PX, math.ceil(width_px * (height_twips / width_twips))))
            buffer = (ctypes.c_ubyte * (width_px * height_px * 4))()
            self._class.paintTile(
                self._document,
                buffer,
                width_px,
                height_px,
                int(rectangle["x"]),
                int(rectangle["y"]),
                width_twips,
                height_twips,
            )
            png = _png_from_lok_buffer(buffer, width_px, height_px, tile_mode)
            tiles.append({
                "index": index,
                "kind": "lok-tile",
                "width": width_px,
                "height": height_px,
                "twips": rectangle,
                "image": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}",
            })
        return tiles

    def post_uno_command(self, command: str, arguments: dict[str, Any] | str | None = None, notify: bool = True) -> dict[str, Any]:
        normalized = normalize_uno_command(command)
        payload = _encode_arguments(arguments)
        self._class.postUnoCommand(
            self._document,
            normalized.encode("utf-8"),
            payload,
            bool(notify),
        )
        return {"ok": True, "native": True, "command": normalized}

    def post_key_event(self, kind: str, char_code: int = 0, key_code: int = 0) -> dict[str, Any]:
        event_type = 1 if str(kind or "").lower() in {"up", "keyup"} else 0
        self._class.postKeyEvent(self._document, event_type, int(char_code or 0), int(key_code or 0))
        return {"ok": True, "native": True, "event": "key", "type": event_type}

    def type_text(self, text: str) -> dict[str, Any]:
        inserted = 0
        for character in str(text or ""):
            code = ord(character)
            self._class.postKeyEvent(self._document, 0, code, code)
            self._class.postKeyEvent(self._document, 1, code, code)
            inserted += 1
        return {"ok": True, "native": True, "event": "text", "inserted": inserted}

    def post_mouse_event(
        self,
        kind: str,
        x: int,
        y: int,
        count: int = 1,
        buttons: int = 1,
        modifier: int = 0,
    ) -> dict[str, Any]:
        mapping = {"down": 0, "mousedown": 0, "up": 1, "mouseup": 1, "move": 2, "mousemove": 2}
        event_type = mapping.get(str(kind or "").lower(), 0)
        self._class.postMouseEvent(
            self._document,
            event_type,
            int(x),
            int(y),
            int(count or 1),
            int(buttons or 1),
            int(modifier or 0),
        )
        return {"ok": True, "native": True, "event": "mouse", "type": event_type}

    def command_values(self, command: str) -> dict[str, Any]:
        normalized = normalize_uno_command(command)
        raw = self._class.getCommandValues(self._document, normalized.encode("utf-8"))
        text = _decode_c_string(raw)
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError:
            parsed = {"raw": text}
        return {"ok": True, "native": True, "command": normalized, "values": parsed}

    def save_as(self, path: str | Path | None = None, fmt: str | None = None) -> bool:
        target = Path(path) if path else self.path
        result = self._class.saveAs(
            self._document,
            str(target).encode("utf-8"),
            fmt.encode("utf-8") if fmt else None,
            None,
        )
        return result != 0

    def save_to_bytes(self, suffix: str = ".docx", fmt: str | None = "docx") -> bytes:
        temp_dir = Path(tempfile.mkdtemp(prefix="a0-lok-save-"))
        try:
            target = temp_dir / f"document{suffix}"
            if not self.save_as(target, fmt):
                raise LibreOfficeKitNativeError("LibreOfficeKit saveAs failed.")
            return target.read_bytes()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def close(self) -> None:
        document = getattr(self, "_document", None)
        if document:
            self._class.destroy(document)
            self._document = None


def normalize_uno_command(command: str) -> str:
    value = str(command or "").strip()
    if not value:
        raise ValueError("UNO command is required.")
    return value if value.startswith(".uno:") else f".uno:{value}"


def _encode_arguments(arguments: dict[str, Any] | str | None) -> bytes | None:
    if arguments is None or arguments == "":
        return None
    if isinstance(arguments, str):
        return arguments.encode("utf-8")
    return json.dumps(arguments, separators=(",", ":")).encode("utf-8")


def _decode_c_string(value: bytes | int | None) -> str:
    if not value:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ctypes.string_at(value).decode("utf-8", errors="replace")


def _parse_rectangles(payload: str) -> list[dict[str, int]]:
    rectangles: list[dict[str, int]] = []
    for item in str(payload or "").split(";"):
        numbers = [part.strip() for part in item.split(",")]
        if len(numbers) < 4:
            continue
        try:
            x, y, width, height = [int(float(value)) for value in numbers[:4]]
        except ValueError:
            continue
        if width > 0 and height > 0:
            rectangles.append({"x": x, "y": y, "width": width, "height": height})
    return rectangles


def _png_from_lok_buffer(buffer: Any, width: int, height: int, tile_mode: int) -> bytes:
    raw = bytes(buffer)
    rows = []
    stride = width * 4
    for y in range(height):
        source = raw[y * stride:(y + 1) * stride]
        if tile_mode == 1:
            row = bytearray(stride)
            for index in range(0, stride, 4):
                blue = source[index]
                green = source[index + 1]
                red = source[index + 2]
                alpha = source[index + 3]
                row[index:index + 4] = bytes((red, green, blue, alpha))
            source = bytes(row)
        rows.append(b"\x00" + source)
    return _png_rgba(width, height, b"".join(rows))


def _png_rgba(width: int, height: int, scanlines: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b"")
