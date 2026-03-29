from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from rest_framework.exceptions import ValidationError

ALLOWED_NODES = {
    "doc",
    "paragraph",
    "heading",
    "blockquote",
    "text",
    "hard_break",
    "ordered_list",
    "bullet_list",
    "task_list",
    "task_item",
    "list_item",
    "code_block",
    "horizontal_rule",
    "image",
    "video_embed",
    "table",
    "table_row",
    "table_cell",
    "table_header",
    "callout",
    "details",
    "summary",
    "badge",
    "mention",
    "hashtag",
    "link",
}

ALLOWED_MARKS = {
    "bold",
    "italic",
    "underline",
    "strikethrough",
    "superscript",
    "subscript",
    "inline_code",
    "text_color",
    "highlight",
    "background_color",
    "font_size",
    "font_family",
    "letter_spacing",
    "line_height",
    "text_align",
    "small_caps",
    "badge",
    "link",
    "mention",
    "hashtag",
}

MAX_DOC_DEPTH = 12
MAX_NODES = 600
MAX_TEXT_LENGTH = 16_000
TEXT_PREVIEW_LIMIT = 200


def process_rich_text_document(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    if not isinstance(doc, dict):
        raise ValidationError("text must be a JSON object representing the document.")
    if doc.get("type") != "doc":
        raise ValidationError("document root must have type='doc'.")

    text_parts: List[str] = []
    node_counter = 0

    def _collect_text(node: Dict[str, Any], depth: int):
        nonlocal node_counter
        node_counter += 1
        if node_counter > MAX_NODES:
            raise ValidationError("Document contains too many nodes.")
        if depth > MAX_DOC_DEPTH:
            raise ValidationError("Document nesting is too deep.")

        node_type = node.get("type")
        if node_type not in ALLOWED_NODES:
            raise ValidationError(f"Unsupported node type: {node_type}")

        if node_type == "text":
            text_value = node.get("text")
            if not isinstance(text_value, str):
                raise ValidationError("Text nodes must include a string 'text' attribute.")
            _validate_marks(node.get("marks", []))
            if text_value:
                text_parts.append(text_value)
            return

        if node_type in {"hard_break", "horizontal_rule"}:
            text_parts.append("\n")

        for child in node.get("content", []) or []:
            _collect_text(child, depth + 1)
        if node_type in {"paragraph", "heading", "code_block", "blockquote"}:
            text_parts.append("\n")

    def _validate_marks(marks: Iterable[Any]):
        if not isinstance(marks, list):
            raise ValidationError("marks must be a list")
        for mark in marks:
            if not isinstance(mark, dict):
                raise ValidationError("Each mark must be an object.")
            mark_type = mark.get("type")
            if mark_type not in ALLOWED_MARKS:
                raise ValidationError(f"Unsupported mark: {mark_type}")
            attrs = mark.get("attrs")
            if attrs is not None and not isinstance(attrs, dict):
                raise ValidationError("Mark attrs must be an object.")

    _collect_text(doc, 0)

    plain = "".join(text_parts).strip()
    if len(plain) > MAX_TEXT_LENGTH:
        raise ValidationError("Document text is too long.")
    preview = plain[:TEXT_PREVIEW_LIMIT]
    return doc, plain, preview


def build_plain_text_document(text: str) -> Dict[str, Any]:
    paragraph: Dict[str, Any] = {"type": "paragraph", "content": []}
    if text:
        paragraph["content"].append({"type": "text", "text": text})
    return {"type": "doc", "content": [paragraph]}
