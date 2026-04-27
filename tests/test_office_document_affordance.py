from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._office.helpers import document_affordance


def substantial_text(prefix: str = "Here is the material.") -> str:
    paragraph = (
        "This section gives concrete context, constraints, tradeoffs, and next steps "
        "so the artifact has enough substance to be useful in a real collaboration. "
    )
    return f"{prefix}\n\n" + paragraph * 8


def standalone_report() -> str:
    paragraph = (
        "The team should align the operating model, clarify ownership, and preserve "
        "a concise decision trail so execution remains calm, inspectable, and repeatable. "
    )
    return (
        "# Retention Report\n\n"
        "## Executive Summary\n"
        f"{paragraph * 4}\n\n"
        "## Recommendations\n"
        f"{paragraph * 4}"
    )


def test_explicit_docx_request_creates_document_artifact():
    decision = document_affordance.decide_response_artifact(
        "Please create a DOCX report for the leadership review.",
        substantial_text(),
    )

    assert decision is not None
    assert decision.kind == "document"
    assert decision.fmt == "docx"
    assert decision.reason == "explicit_handoff"


def test_explicit_spreadsheet_file_request_creates_spreadsheet_artifact():
    decision = document_affordance.decide_response_artifact(
        "Build an editable spreadsheet file for this budget.",
        substantial_text(),
    )

    assert decision is not None
    assert decision.kind == "spreadsheet"
    assert decision.fmt == "xlsx"
    assert decision.reason == "explicit_handoff"


def test_convert_into_document_creates_document_artifact():
    decision = document_affordance.decide_response_artifact(
        "Convert this into a document.",
        substantial_text(),
    )

    assert decision is not None
    assert decision.kind == "document"
    assert decision.reason == "explicit_handoff"


def test_long_document_topic_does_not_create_artifact_without_handoff_signal():
    decision = document_affordance.decide_response_artifact(
        "Write a detailed explanation of the document handoff implementation.",
        substantial_text(),
    )

    assert decision is None


def test_long_policy_question_does_not_create_artifact_without_create_intent():
    decision = document_affordance.decide_response_artifact(
        "What should our remote-work policy say about async updates?",
        substantial_text(),
    )

    assert decision is None


def test_office_as_workplace_topic_is_not_a_handoff_signal():
    decision = document_affordance.decide_response_artifact(
        "Write a memo about office etiquette.",
        substantial_text(),
    )

    assert decision is None


def test_deliverable_request_needs_standalone_artifact_shape():
    decision = document_affordance.decide_response_artifact(
        "Draft a report about retention risks.",
        substantial_text(),
    )

    assert decision is None


def test_deliverable_request_with_artifact_shape_creates_document_artifact():
    decision = document_affordance.decide_response_artifact(
        "Draft a report about retention risks.",
        standalone_report(),
    )

    assert decision is not None
    assert decision.kind == "document"
    assert decision.reason == "document_intent"


def test_chat_only_instruction_blocks_even_explicit_file_request():
    decision = document_affordance.decide_response_artifact(
        "Create a DOCX report, but just answer in chat.",
        standalone_report(),
    )

    assert decision is None
