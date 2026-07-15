"""Document-level undo/redo history for text-block edits."""

from __future__ import annotations

import copy
from dataclasses import dataclass

import fitz

from .models import TextBlock, TextStyleSpan


@dataclass
class HistorySnapshot:
    """Frozen document edit state that can be restored."""

    blocks: list[TextBlock]
    selected_block_id: int | None
    current_page_index: int
    insert_mode: bool = False


@dataclass
class HistoryEntry:
    label: str
    snapshot: HistorySnapshot


def clone_style_span(span: TextStyleSpan) -> TextStyleSpan:
    return TextStyleSpan(
        span.start,
        span.end,
        span.bold,
        span.italic,
        span.font_size,
        span.font_family,
        span.text_color,
    )


def clone_block(block: TextBlock) -> TextBlock:
    """Deep-copy a TextBlock including rects and style spans."""
    style_spans = [clone_style_span(span) for span in (block.style_spans or [])]
    original_style_spans = [clone_style_span(span) for span in (block.original_style_spans or [])]
    cloned = TextBlock(
        id=block.id,
        page_index=block.page_index,
        rect=copy.copy(block.rect),
        text=block.text,
        original_text=block.original_text,
        font_size=block.font_size,
        original_font_size=block.original_font_size,
        font_family=block.font_family,
        original_font_family=block.original_font_family,
        text_color=block.text_color,
        original_text_color=block.original_text_color,
        bold=block.bold,
        original_bold=block.original_bold,
        italic=block.italic,
        original_italic=block.original_italic,
        style_spans=style_spans,
        original_style_spans=original_style_spans,
        deleted=block.deleted,
        inserted=block.inserted,
        manually_resized=block.manually_resized,
        original_rect=copy.copy(block.original_rect) if block.original_rect is not None else None,
    )
    return cloned


def clone_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    return [clone_block(block) for block in blocks]


class EditHistory:
    """Linear undo/redo stack. Each entry stores the state *before* an action."""

    def __init__(self, max_steps: int = 60):
        self.max_steps = max(1, int(max_steps))
        self._undo: list[HistoryEntry] = []
        self._redo: list[HistoryEntry] = []

    def clear(self):
        self._undo.clear()
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def push(self, label: str, before: HistorySnapshot):
        """Record state before a mutation; clears the redo branch."""
        self._undo.append(HistoryEntry(label=label, snapshot=before))
        if len(self._undo) > self.max_steps:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self, current: HistorySnapshot) -> HistorySnapshot | None:
        """
        Undo last action.
        `current` is the live state (pushed onto redo).
        Returns the snapshot to restore, or None.
        """
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(HistoryEntry(label=entry.label, snapshot=current))
        return entry.snapshot

    def redo(self, current: HistorySnapshot) -> HistorySnapshot | None:
        """Redo last undone action."""
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(HistoryEntry(label=entry.label, snapshot=current))
        return entry.snapshot

    def depth(self) -> tuple[int, int]:
        return len(self._undo), len(self._redo)
