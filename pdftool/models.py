import copy
from dataclasses import dataclass

import fitz


@dataclass
class TextStyleSpan:
    start: int
    end: int
    bold: bool = False
    italic: bool = False


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
    text_color: str = "#000000"
    bold: bool = False
    italic: bool = False
    style_spans: list[TextStyleSpan] | None = None
    deleted: bool = False
    inserted: bool = False
    manually_resized: bool = False
    original_rect: fitz.Rect | None = None

    def __post_init__(self):
        if self.original_rect is None:
            self.original_rect = copy.copy(self.rect)
        if self.style_spans is None:
            self.style_spans = []
