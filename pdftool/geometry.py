import fitz


def resize_handle_points(rect: tuple[float, float, float, float]) -> list[tuple[str, float, float]]:
    x0, y0, x1, y1 = rect
    mid_x = (x0 + x1) / 2
    mid_y = (y0 + y1) / 2
    return [
        ("nw", x0, y0),
        ("n", mid_x, y0),
        ("ne", x1, y0),
        ("e", x1, mid_y),
        ("se", x1, y1),
        ("s", mid_x, y1),
        ("sw", x0, y1),
        ("w", x0, mid_y),
    ]


def constrain_rect_to_page(rect: fitz.Rect, page_rect: fitz.Rect, min_width: float = 12.0, min_height: float = 10.0) -> fitz.Rect:
    x0 = max(page_rect.x0, min(rect.x0, page_rect.x1 - min_width))
    y0 = max(page_rect.y0, min(rect.y0, page_rect.y1 - min_height))
    x1 = max(x0 + min_width, min(rect.x1, page_rect.x1))
    y1 = max(y0 + min_height, min(rect.y1, page_rect.y1))
    return fitz.Rect(x0, y0, x1, y1)


def constrain_moved_rect_to_page(rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    width = rect.width
    height = rect.height
    x0 = min(max(rect.x0, page_rect.x0), max(page_rect.x0, page_rect.x1 - width))
    y0 = min(max(rect.y0, page_rect.y0), max(page_rect.y0, page_rect.y1 - height))
    return fitz.Rect(x0, y0, x0 + width, y0 + height)

