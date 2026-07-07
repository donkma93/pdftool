import copy

import fitz

from .font_manager import font_file_for_style
from .models import TextBlock, TextStyleSpan
from .text_layout import required_text_height, wrap_text_for_pdf


def extract_text_blocks(doc: fitz.Document) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    block_id = 1
    for page_index in range(len(doc)):
        page = doc[page_index]
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            lines = []
            font_sizes = []
            for line in block.get("lines", []):
                parts = []
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if span_text.strip():
                        parts.append(span_text)
                        font_sizes.append(float(span.get("size", 11)))
                if parts:
                    lines.append("".join(parts).strip())
            text = "\n".join(lines).strip()
            if not text:
                continue
            font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 11.0
            normalized_font_size = max(6.0, min(font_size, 72.0))
            blocks.append(
                TextBlock(
                    block_id,
                    page_index,
                    fitz.Rect(block["bbox"]),
                    text,
                    text,
                    normalized_font_size,
                    normalized_font_size,
                )
            )
            block_id += 1
    return blocks


def expanded_text_rect(block: TextBlock, doc: fitz.Document | None) -> fitz.Rect:
    rect = copy.copy(block.rect)
    if doc is None or block.deleted or not block.text.strip():
        return rect
    if block.manually_resized:
        return rect

    page_rect = doc[block.page_index].rect
    text_width = max(20.0, rect.width - 2)
    required_height = required_text_height(block.text, block.font_size, text_width)
    expanded_height = max(rect.height, required_height + 4)
    rect.y1 = min(page_rect.y1 - 2, rect.y0 + expanded_height)
    return rect


def original_erase_rect(block: TextBlock) -> fitz.Rect:
    if block.original_rect is not None:
        return copy.copy(block.original_rect)
    return copy.copy(block.rect)


def padded_rect(rect: fitz.Rect, doc: fitz.Document | None, page_index: int, padding: float) -> fitz.Rect:
    padded = copy.copy(rect)
    if doc is None:
        return padded
    page_rect = doc[page_index].rect
    padded.x0 = max(page_rect.x0, padded.x0 - padding)
    padded.y0 = max(page_rect.y0, padded.y0 - padding)
    padded.x1 = min(page_rect.x1, padded.x1 + padding)
    padded.y1 = min(page_rect.y1, padded.y1 + padding)
    return padded


def apply_blocks_to_pdf(
    doc: fitz.Document,
    blocks: list[TextBlock],
    source_doc: fitz.Document | None,
    font_files: dict[str, str],
    pdf_font_path: str | None,
):
    changed_blocks = [
        block
        for block in blocks
        if (
            block.deleted
            or (block.inserted and block.text.strip())
            or block.manually_resized
            or block.text != block.original_text
            or abs(block.font_size - block.original_font_size) >= 0.01
            or block.font_family != "Arial"
            or block.text_color != "#000000"
            or block.bold
            or block.italic
            or bool(block.style_spans)
        )
    ]
    for block in changed_blocks:
        page = doc[block.page_index]
        rect = expanded_text_rect(block, source_doc)
        if not block.inserted:
            erase_rect = original_erase_rect(block) if block.manually_resized else rect
            page.draw_rect(
                padded_rect(erase_rect, source_doc, block.page_index, 1),
                color=(1, 1, 1),
                fill=(1, 1, 1),
                overlay=True,
            )
        if block.deleted or not block.text.strip():
            continue
        inset = 0 if block.inserted else 1
        text_rect = fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset)
        insert_wrapped_text(
            page,
            text_rect,
            block.text,
            block.font_size,
            block.font_family,
            block.text_color,
            block.bold,
            block.italic,
            block.style_spans or [],
            font_files,
            pdf_font_path,
        )


def insert_wrapped_text(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_size: float,
    font_family: str,
    text_color: str,
    bold: bool,
    italic: bool,
    style_spans: list[TextStyleSpan],
    font_files: dict[str, str],
    pdf_font_path: str | None,
):
    lines = styled_wrapped_lines(text, font_size, rect.width, bold, italic, style_spans)
    y = rect.y0 + font_size
    line_height = font_size * 1.25

    for line in lines:
        if y > rect.y1:
            break
        x = rect.x0
        for segment in line:
            segment_text = segment["text"]
            if not segment_text:
                continue
            kwargs = text_insert_kwargs(font_size, font_family, text_color, segment["bold"], segment["italic"], font_files, pdf_font_path)
            page.insert_text(fitz.Point(x, y), segment_text, **kwargs)
            x += estimated_text_width(segment_text, font_size)
        y += line_height


def text_insert_kwargs(
    font_size: float,
    font_family: str,
    text_color: str,
    bold: bool,
    italic: bool,
    font_files: dict[str, str],
    pdf_font_path: str | None,
) -> dict:
    kwargs = {
        "fontsize": font_size,
        "color": hex_to_rgb(text_color),
        "overlay": True,
    }
    font_path = font_file_for_style(font_files, font_family, bold, italic) or pdf_font_path
    if font_path:
        kwargs["fontfile"] = font_path
        style_suffix = ("Bold" if bold else "") + ("Italic" if italic else "")
        safe_font_name = "PDFTOOL" + "".join(ch for ch in f"{font_family}{style_suffix}" if ch.isalnum())
        kwargs["fontname"] = safe_font_name or "PDFTOOLUnicode"
    else:
        kwargs["fontname"] = "helv"
    return kwargs


def styled_wrapped_lines(
    text: str,
    font_size: float,
    max_width: float,
    default_bold: bool,
    default_italic: bool,
    style_spans: list[TextStyleSpan],
) -> list[list[dict]]:
    average_char_width = max(3.0, font_size * 0.52)
    max_chars = max(1, int(max_width / average_char_width))
    lines: list[list[dict]] = []
    offset = 0
    for raw_line in text.splitlines() or [text]:
        raw_length = len(raw_line)
        if raw_length == 0:
            lines.append([{"text": "", "bold": default_bold, "italic": default_italic}])
            offset += 1
            continue
        line_start = 0
        while line_start < raw_length:
            line_end = min(raw_length, line_start + max_chars)
            if line_end < raw_length:
                break_at = raw_line.rfind(" ", line_start, line_end + 1)
                if break_at > line_start:
                    line_end = break_at
            lines.append(style_segments_for_range(raw_line, offset + line_start, line_start, line_end, default_bold, default_italic, style_spans))
            line_start = line_end
            while line_start < raw_length and raw_line[line_start] == " ":
                line_start += 1
        offset += raw_length + 1
    return lines


def style_segments_for_range(
    line_text: str,
    absolute_line_start: int,
    start: int,
    end: int,
    default_bold: bool,
    default_italic: bool,
    style_spans: list[TextStyleSpan],
) -> list[dict]:
    segments: list[dict] = []
    current_text = ""
    current_style: tuple[bool, bool] | None = None
    for local_index in range(start, end):
        absolute_index = absolute_line_start + (local_index - start)
        style = style_at_index(absolute_index, default_bold, default_italic, style_spans)
        if current_style is not None and style != current_style:
            segments.append({"text": current_text, "bold": current_style[0], "italic": current_style[1]})
            current_text = ""
        current_style = style
        current_text += line_text[local_index]
    if current_style is None:
        return [{"text": "", "bold": default_bold, "italic": default_italic}]
    segments.append({"text": current_text, "bold": current_style[0], "italic": current_style[1]})
    return segments


def style_at_index(index: int, default_bold: bool, default_italic: bool, style_spans: list[TextStyleSpan]) -> tuple[bool, bool]:
    bold = default_bold
    italic = default_italic
    for span in style_spans:
        if span.start <= index < span.end:
            bold = span.bold
            italic = span.italic
    return bold, italic


def estimated_text_width(text: str, font_size: float) -> float:
    return max(0.0, len(text) * font_size * 0.52)


def hex_to_rgb(color_hex: str) -> tuple[float, float, float]:
    value = color_hex.lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)
