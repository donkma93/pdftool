import copy
from collections import Counter

import fitz

from .font_manager import (
    available_font_files,
    font_file_for_style,
    font_name_suggests_bold,
    font_name_suggests_italic,
    resolve_font_family,
)
from .models import TextBlock, TextStyleSpan
from .text_layout import required_text_height, wrap_text_for_pdf


def color_to_hex(color) -> str:
    """Convert PyMuPDF span color (int or rgb sequence) to #RRGGBB."""
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        channels = list(color[:3])
        if all(isinstance(c, float) and c <= 1.0 for c in channels):
            channels = [int(round(c * 255)) for c in channels]
        else:
            channels = [int(max(0, min(255, c))) for c in channels]
        return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"
    if isinstance(color, int):
        return f"#{color & 0xFFFFFF:06x}"
    return "#000000"


def span_is_bold(span: dict) -> bool:
    flags = int(span.get("flags", 0) or 0)
    font_name = span.get("font") or ""
    return bool(flags & 2**4) or font_name_suggests_bold(font_name)


def span_is_italic(span: dict) -> bool:
    flags = int(span.get("flags", 0) or 0)
    font_name = span.get("font") or ""
    return bool(flags & 2**1) or font_name_suggests_italic(font_name)


def extract_text_blocks(doc: fitz.Document, font_files: dict[str, str] | None = None) -> list[TextBlock]:
    """Extract text blocks with original layout metrics and formatting."""
    font_files = font_files or available_font_files()
    blocks: list[TextBlock] = []
    block_id = 1
    for page_index in range(len(doc)):
        page = doc[page_index]
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue

            line_payloads: list[tuple[str, list[TextStyleSpan]]] = []
            font_sizes: list[float] = []
            families: list[str] = []
            colors: list[str] = []
            bold_votes = 0
            italic_votes = 0

            for line in block.get("lines", []):
                pieces: list[str] = []
                local_spans: list[TextStyleSpan] = []
                cursor = 0
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if not span_text:
                        continue
                    family = resolve_font_family(span.get("font"), font_files)
                    size = max(6.0, min(72.0, float(span.get("size", 11) or 11)))
                    color = color_to_hex(span.get("color", 0))
                    bold = span_is_bold(span)
                    italic = span_is_italic(span)
                    start = cursor
                    end = cursor + len(span_text)
                    local_spans.append(
                        TextStyleSpan(
                            start=start,
                            end=end,
                            bold=bold,
                            italic=italic,
                            font_size=size,
                            font_family=family,
                            text_color=color,
                        )
                    )
                    pieces.append(span_text)
                    cursor = end
                    if span_text.strip():
                        weight = max(1, len(span_text.strip()))
                        font_sizes.append(size)
                        families.append(family)
                        colors.append(color)
                        bold_votes += int(bold) * weight
                        italic_votes += int(italic) * weight

                line_text = "".join(pieces).rstrip()
                if not line_text.strip():
                    continue
                clipped = [
                    TextStyleSpan(
                        s.start,
                        min(s.end, len(line_text)),
                        s.bold,
                        s.italic,
                        s.font_size,
                        s.font_family,
                        s.text_color,
                    )
                    for s in local_spans
                    if s.start < len(line_text)
                ]
                line_payloads.append((line_text, clipped))

            if not line_payloads:
                continue

            style_spans: list[TextStyleSpan] = []
            absolute = 0
            line_texts: list[str] = []
            for index, (line_text, local_spans) in enumerate(line_payloads):
                for span in local_spans:
                    style_spans.append(
                        TextStyleSpan(
                            absolute + span.start,
                            absolute + span.end,
                            span.bold,
                            span.italic,
                            span.font_size,
                            span.font_family,
                            span.text_color,
                        )
                    )
                line_texts.append(line_text)
                absolute += len(line_text)
                if index < len(line_payloads) - 1:
                    absolute += 1  # newline between lines

            text = "\n".join(line_texts)
            font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 11.0
            normalized_font_size = max(6.0, min(font_size, 72.0))
            family = Counter(families).most_common(1)[0][0] if families else resolve_font_family(None, font_files)
            color = Counter(colors).most_common(1)[0][0] if colors else "#000000"
            total_weight = max(1, sum(max(1, len(line.strip())) for line, _ in line_payloads))
            bold = bold_votes * 2 >= total_weight
            italic = italic_votes * 2 >= total_weight

            blocks.append(
                TextBlock(
                    id=block_id,
                    page_index=page_index,
                    rect=fitz.Rect(block["bbox"]),
                    text=text,
                    original_text=text,
                    font_size=normalized_font_size,
                    original_font_size=normalized_font_size,
                    font_family=family,
                    original_font_family=family,
                    text_color=color,
                    original_text_color=color,
                    bold=bold,
                    original_bold=bold,
                    italic=italic,
                    original_italic=italic,
                    style_spans=style_spans,
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


def _style_spans_equal(a: list[TextStyleSpan] | None, b: list[TextStyleSpan] | None) -> bool:
    left = a or []
    right = b or []
    if len(left) != len(right):
        return False
    for sa, sb in zip(left, right):
        if (
            sa.start != sb.start
            or sa.end != sb.end
            or sa.bold != sb.bold
            or sa.italic != sb.italic
            or (sa.font_size or 0) != (sb.font_size or 0)
            or (sa.font_family or "") != (sb.font_family or "")
            or (sa.text_color or "") != (sb.text_color or "")
        ):
            return False
    return True


def block_has_changes(block: TextBlock) -> bool:
    return (
        block.deleted
        or (block.inserted and bool(block.text.strip()))
        or block.manually_resized
        or block.text != block.original_text
        or abs(block.font_size - block.original_font_size) >= 0.01
        or block.font_family != block.original_font_family
        or block.text_color.lower() != block.original_text_color.lower()
        or block.bold != block.original_bold
        or block.italic != block.original_italic
        or not _style_spans_equal(block.style_spans, block.original_style_spans)
    )


def changed_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    return [block for block in blocks if block_has_changes(block)]


def apply_blocks_to_pdf(
    doc: fitz.Document,
    blocks: list[TextBlock],
    source_doc: fitz.Document | None,
    font_files: dict[str, str],
    pdf_font_path: str | None,
):
    for block in changed_blocks(blocks):
        page = doc[block.page_index]
        rect = expanded_text_rect(block, source_doc)
        if not block.inserted:
            erase_rect = original_erase_rect(block) if block.manually_resized else rect
            page.add_redact_annot(
                padded_rect(erase_rect, source_doc, block.page_index, 1),
                fill=None,
                cross_out=False,
            )
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
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
