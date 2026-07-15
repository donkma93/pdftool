import fitz


def wrap_text_for_canvas(text: str, font_size: int, max_width: float) -> list[str]:
    average_char_width = max(4, font_size * 0.55)
    max_chars = max(1, int(max_width / average_char_width))
    return wrap_text_by_character_estimate(text, max_chars)


def wrap_text_for_pdf(text: str, font_size: float, max_width: float) -> list[str]:
    average_char_width = max(3.0, font_size * 0.52)
    max_chars = max(1, int(max_width / average_char_width))
    return wrap_text_by_character_estimate(text, max_chars)


def wrap_text_by_character_estimate(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines() or [text]:
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def required_text_height(text: str, font_size: float, max_width: float) -> float:
    lines = wrap_text_for_pdf(text, font_size, max_width)
    return max(1, len(lines)) * font_size * 1.25


def fit_font_size(rect: fitz.Rect, text: str, preferred_size: float) -> float:
    if not text.strip() or rect.width <= 1 or rect.height <= 1:
        return max(6.0, min(72.0, preferred_size))

    min_size = 6.0
    max_size = max(min_size, min(72.0, preferred_size))
    if required_text_height(text, min_size, rect.width) > rect.height:
        return min_size

    low = min_size
    high = max_size
    for _ in range(12):
        mid = (low + high) / 2
        if required_text_height(text, mid, rect.width) <= rect.height:
            low = mid
        else:
            high = mid
    return max(min_size, min(max_size, low))
