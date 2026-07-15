import copy
from dataclasses import dataclass

import fitz


@dataclass
class TextStyleSpan:
    start: int
    end: int
    bold: bool = False
    italic: bool = False
    font_size: float | None = None
    font_family: str | None = None
    text_color: str | None = None


@dataclass
class TextBlock:
    id: int
    page_index: int
    rect: fitz.Rect
    text: str
    original_text: str
    font_size: float
    original_font_size: float = 11.0
    font_family: str = "Arial"
    original_font_family: str = "Arial"
    text_color: str = "#000000"
    original_text_color: str = "#000000"
    bold: bool = False
    original_bold: bool = False
    italic: bool = False
    original_italic: bool = False
    style_spans: list[TextStyleSpan] | None = None
    original_style_spans: list[TextStyleSpan] | None = None
    deleted: bool = False
    inserted: bool = False
    manually_resized: bool = False
    original_rect: fitz.Rect | None = None

    def __post_init__(self):
        if self.original_rect is None:
            self.original_rect = copy.copy(self.rect)
        if self.style_spans is None:
            self.style_spans = []
        if self.original_style_spans is None:
            self.original_style_spans = [copy.copy(span) for span in (self.style_spans or [])]

    def clone_style_spans(self) -> list[TextStyleSpan]:
        return [
            TextStyleSpan(
                span.start,
                span.end,
                span.bold,
                span.italic,
                span.font_size,
                span.font_family,
                span.text_color,
            )
            for span in (self.style_spans or [])
        ]
