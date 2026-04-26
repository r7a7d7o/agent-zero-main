from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


MIN_ARTIFACT_CHARS = 700
MIN_ARTIFACT_WORDS = 110
MIN_EXPLICIT_ARTIFACT_CHARS = 240
MIN_EXPLICIT_ARTIFACT_WORDS = 35

CREATE_TERMS = {
    "write",
    "draft",
    "compose",
    "create",
    "generate",
    "prepare",
    "produce",
    "make",
    "build",
    "author",
}

DOCUMENT_TERMS = {
    "article",
    "brief",
    "contract",
    "cv",
    "doc",
    "document",
    "docx",
    "draft",
    "essay",
    "guide",
    "letter",
    "manual",
    "memo",
    "policy",
    "proposal",
    "report",
    "resume",
    "spec",
    "story",
    "whitepaper",
}

SPREADSHEET_TERMS = {
    "budget",
    "excel",
    "sheet",
    "spreadsheet",
    "table",
    "workbook",
    "xlsx",
}

PRESENTATION_TERMS = {
    "deck",
    "ppt",
    "pptx",
    "presentation",
    "slide",
    "slides",
}

CHAT_ONLY_TERMS = {
    "answer in chat",
    "in chat",
    "just answer",
    "just reply",
    "no file",
    "no files",
}

SKIP_RESPONSE_PREFIXES = (
    "i can't",
    "i cannot",
    "i'm sorry",
    "sorry,",
    "i can help",
)


@dataclass(frozen=True)
class ArtifactDecision:
    kind: str
    fmt: str
    title: str
    content: str
    reason: str


def decide_response_artifact(user_message: Any, response_text: str) -> ArtifactDecision | None:
    user_text = flatten_text(user_message).strip()
    response_text = str(response_text or "").strip()
    if not user_text or not response_text:
        return None

    lowered_user = normalize_text(user_text)
    lowered_response = normalize_text(response_text[:240])
    if any(term in lowered_user for term in CHAT_ONLY_TERMS):
        return None
    if lowered_response.startswith(SKIP_RESPONSE_PREFIXES):
        return None
    if looks_like_tool_or_status_response(response_text):
        return None

    kind, fmt, explicit_artifact = infer_kind_and_format(lowered_user)
    if not explicit_artifact and not has_document_creation_intent(lowered_user):
        return None

    if not is_substantial(response_text, explicit_artifact):
        return None

    title = infer_title(user_text, response_text, kind)
    return ArtifactDecision(
        kind=kind,
        fmt=fmt,
        title=title,
        content=response_text,
        reason="explicit" if explicit_artifact else "document_intent",
    )


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        preferred_keys = ("user_message", "user_intervention", "message", "content", "text")
        skipped_keys = {*preferred_keys, "attachments", "system_message", "raw_content"}
        preferred = []
        for key in preferred_keys:
            if key in value:
                preferred.append(flatten_text(value[key]))
        remaining = [
            flatten_text(child)
            for key, child in value.items()
            if key not in skipped_keys
        ]
        return "\n".join(part for part in [*preferred, *remaining] if part)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(part for item in value if (part := flatten_text(item)))
    return str(value)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def infer_kind_and_format(lowered_user: str) -> tuple[str, str, bool]:
    explicit = False
    if has_any(lowered_user, PRESENTATION_TERMS):
        explicit = True
        return "presentation", "pptx", explicit
    if has_any(lowered_user, SPREADSHEET_TERMS):
        explicit = True
        return "spreadsheet", "xlsx", explicit
    if has_any(lowered_user, DOCUMENT_TERMS):
        explicit = True
    return "document", "docx", explicit


def has_document_creation_intent(lowered_user: str) -> bool:
    return has_any(lowered_user, CREATE_TERMS) and has_any(
        lowered_user,
        DOCUMENT_TERMS | SPREADSHEET_TERMS | PRESENTATION_TERMS,
    )


def has_any(text: str, terms: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def is_substantial(text: str, explicit_artifact: bool) -> bool:
    word_count = len(re.findall(r"\w+", text))
    char_count = len(text)
    if explicit_artifact:
        return char_count >= MIN_EXPLICIT_ARTIFACT_CHARS and word_count >= MIN_EXPLICIT_ARTIFACT_WORDS
    return char_count >= MIN_ARTIFACT_CHARS and word_count >= MIN_ARTIFACT_WORDS


def looks_like_tool_or_status_response(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("{") and '"tool_name"' in stripped[:300]:
        return True
    if "/a0/usr/workdir/documents/" in stripped:
        return True
    return False


def infer_title(user_text: str, response_text: str, kind: str) -> str:
    response_title = title_from_response(response_text)
    if response_title:
        return response_title

    request_title = title_from_request(user_text)
    if request_title:
        return request_title

    return {
        "spreadsheet": "Spreadsheet",
        "presentation": "Presentation",
    }.get(kind, "Document")


def title_from_response(response_text: str) -> str:
    for raw_line in response_text.splitlines()[:8]:
        line = raw_line.strip()
        if not line:
            continue
        for pattern in (
            r"^#{1,3}\s+(.+?)\s*$",
            r"^\*\*(.+?)\*\*\s*$",
            r"^__(.+?)__\s*$",
        ):
            match = re.match(pattern, line)
            if match:
                return clean_title(match.group(1))
        if len(line) <= 80 and not line.endswith((".", "?", "!", ":")):
            return clean_title(line)
        break
    return ""


def title_from_request(user_text: str) -> str:
    text = re.sub(r"\s+", " ", user_text).strip()
    quoted = re.search(r"[\"'“”](.{4,90}?)[\"'“”]", text)
    if quoted:
        return clean_title(quoted.group(1))

    cleaned = re.sub(
        r"\b(write|draft|compose|create|generate|prepare|produce|make|build|author)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(a|an|the|new|for me|please|docx|document|file)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = clean_title(cleaned)
    return cleaned if 4 <= len(cleaned) <= 80 else ""


def clean_title(value: str) -> str:
    value = re.sub(r"[*_`#>\[\]{}]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .:-")
    return value[:90].strip(" .:-")


def format_created_response(basename: str, path: str) -> str:
    return (
        f"Created **{basename}** and opened it in the Office canvas.\n\n"
        f"Path: `{path}`"
    )
