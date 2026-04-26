from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from helpers import files, plugins
from plugins._browser.helpers.config import PLUGIN_NAME, get_browser_config


EXTENSIONS_ROOT_DIR = ("usr", "plugins", PLUGIN_NAME, "extensions")
EXTENSION_ID_RE = re.compile(r"^[a-p]{32}$")
WEB_STORE_ID_RE = re.compile(r"(?<![a-p])([a-p]{32})(?![a-p])")
CHROME_VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,3})")
CHROME_I18N_MESSAGE_RE = re.compile(r"__MSG_([A-Za-z0-9_@.-]+)__")
DEFAULT_CHROME_PRODVERSION = "140.0.0.0"
CHROME_VERSION_COMMANDS = (
    ("google-chrome", "--version"),
    ("chromium", "--version"),
    ("chromium-browser", "--version"),
)
WEB_STORE_DOWNLOAD_URL = (
    "https://clients2.google.com/service/update2/crx"
    "?response=redirect"
    "&prod=chromecrx"
    "&prodversion={prodversion}"
    "&acceptformat=crx2,crx3"
    "&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"
)


def get_extensions_root() -> Path:
    root = Path(files.get_abs_path(*EXTENSIONS_ROOT_DIR))
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_chrome_web_store_extension_id(value: str) -> str:
    source = str(value or "").strip()
    if EXTENSION_ID_RE.fullmatch(source):
        return source

    match = WEB_STORE_ID_RE.search(source)
    if match:
        return match.group(1)

    raise ValueError("Enter a Chrome Web Store URL or a 32-character extension id.")


def list_browser_extensions() -> list[dict[str, Any]]:
    config = get_browser_config()
    enabled_paths = {str(Path(path).expanduser()) for path in config["extension_paths"]}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    root = get_extensions_root()
    if root.exists():
        for manifest_path in sorted(root.glob("**/manifest.json")):
            entry = _extension_entry(manifest_path.parent, enabled_paths)
            seen.add(entry["path"])
            entries.append(entry)

    for configured_path in config["extension_paths"]:
        extension_dir = Path(configured_path).expanduser()
        extension_path = str(extension_dir)
        if extension_path in seen or not (extension_dir / "manifest.json").is_file():
            continue
        entries.append(_extension_entry(extension_dir, enabled_paths))
        seen.add(extension_path)

    return entries


def install_chrome_web_store_extension(source: str) -> dict[str, Any]:
    extension_id = parse_chrome_web_store_extension_id(source)
    target = get_extensions_root() / "chrome-web-store" / extension_id

    with tempfile.TemporaryDirectory(prefix="a0-browser-ext-") as tmp:
        archive_path = Path(tmp) / f"{extension_id}.crx"
        _download_crx(extension_id, archive_path)
        payload_path = Path(tmp) / f"{extension_id}.zip"
        payload_path.write_bytes(_crx_zip_payload(archive_path.read_bytes()))
        extracted_path = Path(tmp) / "extracted"
        _safe_extract_zip(payload_path, extracted_path)

        if not (extracted_path / "manifest.json").is_file():
            raise ValueError("Downloaded extension did not contain a manifest.json file.")

        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted_path, target)

    config = _enable_extension_path(target)
    manifest = _read_manifest(target)
    return {
        "ok": True,
        "id": extension_id,
        "name": _manifest_label(target, manifest, "name") or extension_id,
        "version": manifest.get("version") or "",
        "path": str(target),
        "extension_paths": config["extension_paths"],
    }


def set_browser_extension_enabled(extension_path: str, enabled: bool) -> dict[str, Any]:
    raw_path = str(extension_path or "").strip()
    if not raw_path:
        raise ValueError("Choose an extension first.")

    path = str(Path(raw_path).expanduser())
    directory = Path(path)
    if enabled and not (directory / "manifest.json").is_file():
        raise ValueError("Extension folder must contain a manifest.json file.")

    config = get_browser_config()
    paths = list(config["extension_paths"])
    if enabled:
        if path not in paths:
            paths.append(path)
    else:
        paths = [item for item in paths if str(Path(item).expanduser()) != path]

    config["extension_paths"] = paths
    plugins.save_plugin_config(PLUGIN_NAME, "", "", config)
    return config


def _download_crx(extension_id: str, archive_path: Path) -> None:
    prodversion = _detect_chrome_prodversion()
    url = _build_web_store_download_url(extension_id, prodversion=prodversion)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{prodversion} Safari/537.36"
            )
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as exc:
        raise ValueError(
            f"Chrome Web Store download failed with HTTP {exc.code} for Chrome {prodversion}."
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ValueError(f"Chrome Web Store download failed: {reason}.") from exc

    with response:
        status = response.getcode()
        data = response.read()
    if not data:
        raise ValueError(
            "Chrome Web Store did not return an extension package "
            f"(HTTP {status}, Chrome {prodversion})."
        )
    archive_path.write_bytes(data)


def _build_web_store_download_url(extension_id: str, *, prodversion: str | None = None) -> str:
    return WEB_STORE_DOWNLOAD_URL.format(
        extension_id=extension_id,
        prodversion=_normalize_chrome_prodversion(prodversion or "") or DEFAULT_CHROME_PRODVERSION,
    )


def _detect_chrome_prodversion() -> str:
    env_version = _normalize_chrome_prodversion(os.environ.get("A0_BROWSER_EXTENSION_PRODVERSION", ""))
    if env_version:
        return env_version

    for command in CHROME_VERSION_COMMANDS:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue

        version = _normalize_chrome_prodversion(
            " ".join(part for part in (completed.stdout, completed.stderr) if part)
        )
        if version:
            return version

    return DEFAULT_CHROME_PRODVERSION


def _normalize_chrome_prodversion(value: str) -> str:
    match = CHROME_VERSION_RE.search(str(value or ""))
    if not match:
        return ""
    parts = match.group(1).split(".")
    return ".".join((parts + ["0", "0", "0", "0"])[:4])


def _crx_zip_payload(data: bytes) -> bytes:
    if data.startswith(b"PK"):
        return data
    if data[:4] != b"Cr24":
        raise ValueError("Downloaded package is not a CRX or ZIP archive.")

    version = int.from_bytes(data[4:8], "little")
    if version == 2:
        public_key_len = int.from_bytes(data[8:12], "little")
        signature_len = int.from_bytes(data[12:16], "little")
        offset = 16 + public_key_len + signature_len
    elif version == 3:
        header_len = int.from_bytes(data[8:12], "little")
        offset = 12 + header_len
    else:
        raise ValueError(f"Unsupported CRX version: {version}.")

    payload = data[offset:]
    if not payload.startswith(b"PK"):
        raise ValueError("CRX payload did not contain a ZIP archive.")
    return payload


def _safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            if not destination.is_relative_to(root):
                raise ValueError("Extension archive contains an unsafe path.")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _enable_extension_path(extension_path: Path) -> dict[str, Any]:
    config = get_browser_config()
    path = str(extension_path)
    paths = list(config["extension_paths"])
    if path not in paths:
        paths.append(path)
    config["extension_paths"] = paths
    plugins.save_plugin_config(PLUGIN_NAME, "", "", config)
    return config


def _extension_entry(extension_dir: Path, enabled_paths: set[str]) -> dict[str, Any]:
    manifest = _read_manifest(extension_dir)
    extension_path = str(extension_dir)
    name = (
        _manifest_label(extension_dir, manifest, "name")
        or _manifest_label(extension_dir, manifest, "short_name")
        or extension_dir.name
    )
    return {
        "name": name,
        "raw_name": manifest.get("name") or "",
        "description": _manifest_label(extension_dir, manifest, "description"),
        "version": manifest.get("version") or "",
        "path": extension_path,
        "enabled": extension_path in enabled_paths,
    }


def _read_manifest(extension_path: Path) -> dict[str, Any]:
    manifest_path = extension_path / "manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _manifest_label(extension_dir: Path, manifest: dict[str, Any], key: str) -> str:
    value = str(manifest.get(key) or "").strip()
    if not value:
        return ""

    messages = _load_locale_messages(extension_dir, str(manifest.get("default_locale") or ""))
    if not messages:
        return "" if CHROME_I18N_MESSAGE_RE.fullmatch(value) else value

    def replace_message(match: re.Match[str]) -> str:
        message_key = match.group(1)
        message = _resolve_locale_message(messages, message_key)
        return message if message is not None else match.group(0)

    resolved = CHROME_I18N_MESSAGE_RE.sub(replace_message, value).strip()
    if CHROME_I18N_MESSAGE_RE.fullmatch(resolved):
        return ""
    return resolved


def _load_locale_messages(extension_dir: Path, default_locale: str) -> dict[str, Any]:
    locale_root = extension_dir / "_locales"
    if not locale_root.is_dir():
        return {}

    preferred_locales = [
        default_locale,
        default_locale.split("_", 1)[0] if default_locale else "",
        "en_US",
        "en",
    ]
    for locale in [item for item in preferred_locales if item]:
        messages = _read_locale_file(locale_root / locale / "messages.json")
        if messages:
            return messages

    for messages_path in sorted(locale_root.glob("*/messages.json")):
        messages = _read_locale_file(messages_path)
        if messages:
            return messages
    return {}


def _read_locale_file(messages_path: Path) -> dict[str, Any]:
    if not messages_path.is_file():
        return {}
    try:
        data = json.loads(messages_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_locale_message(messages: dict[str, Any], key: str) -> str | None:
    entry = messages.get(key)
    if not isinstance(entry, dict):
        return None
    message = str(entry.get("message") or "")
    if not message:
        return None

    placeholders = entry.get("placeholders")
    if isinstance(placeholders, dict):
        for name, placeholder in placeholders.items():
            if not isinstance(placeholder, dict):
                continue
            content = str(placeholder.get("content") or "")
            if not content:
                continue
            message = re.sub(
                rf"\${re.escape(str(name))}\$",
                content,
                message,
                flags=re.IGNORECASE,
            )
    return message
