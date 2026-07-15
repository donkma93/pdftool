"""
Production-style toolbar icons for PDFTOOL.

Drawn with Tk Canvas primitives (no external assets) using a consistent
20×20 visual grid, dual-tone palette, and filled shapes inspired by
modern desktop apps (Fluent / VS Code density).

Also resolves the application `.ico` for the window and packaged EXE.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path


def icon_palette(active: bool = False) -> dict[str, str]:
    if active:
        return {
            "fg": "#ffffff",
            "muted": "#d4f5e3",
            "accent": "#7dffb0",
            "fill": "#1f4a32",
            "fill2": "#2d6b48",
            "danger": "#ff8f8f",
            "paper": "#f4fff8",
            "ink": "#123524",
        }
    return {
        "fg": "#f0f0f0",
        "muted": "#a8aeb4",
        "accent": "#2ecc71",
        "fill": "#3a3f46",
        "fill2": "#4a515a",
        "danger": "#ff6b6b",
        "paper": "#f7f7f7",
        "ink": "#2b2b2b",
    }


def draw_icon(
    canvas: tk.Canvas,
    name: str,
    width: int = 36,
    height: int = 32,
    active: bool = False,
) -> None:
    """Draw a named icon centered in the canvas."""
    canvas.delete("all")
    p = icon_palette(active)
    cx = width / 2
    cy = height / 2
    # Soft active glow behind icon
    if active:
        canvas.create_oval(cx - 12, cy - 11, cx + 12, cy + 11, fill="#1a3a28", outline="")

    drawer = ICON_DRAWERS.get(name, _draw_fallback)
    drawer(canvas, cx, cy, p)


def _round_rect(canvas, x0, y0, x1, y1, r=2, **kwargs):
    """Approximate rounded rect with polygon + arcs for small toolbar sizes."""
    r = min(r, abs(x1 - x0) / 2, abs(y1 - y0) / 2)
    points = [
        x0 + r, y0,
        x1 - r, y0,
        x1, y0,
        x1, y0 + r,
        x1, y1 - r,
        x1, y1,
        x1 - r, y1,
        x0 + r, y1,
        x0, y1,
        x0, y1 - r,
        x0, y0 + r,
        x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _draw_document(canvas, cx, cy, p):
    # File body
    _round_rect(canvas, cx - 7, cy - 9, cx + 6, cy + 9, r=2, fill=p["paper"], outline=p["muted"], width=1)
    # Folded corner
    canvas.create_polygon(
        cx + 1, cy - 9,
        cx + 6, cy - 9,
        cx + 6, cy - 4,
        fill=p["fill2"], outline=p["muted"], width=1,
    )
    canvas.create_line(cx + 1, cy - 9, cx + 1, cy - 4, cx + 6, cy - 4, fill=p["muted"])
    # Text lines
    for i, y in enumerate((-2, 1, 4)):
        canvas.create_line(cx - 4, cy + y, cx + (3 if i < 2 else 1), cy + y, fill=p["accent"], width=1)
    # PDF badge hint
    canvas.create_rectangle(cx - 5, cy + 6, cx + 4, cy + 9, fill=p["accent"], outline="")


def _draw_close(canvas, cx, cy, p):
    color = p["danger"]
    canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, outline=color, width=1)
    canvas.create_line(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, width=2, capstyle=tk.ROUND)
    canvas.create_line(cx + 4, cy - 4, cx - 4, cy + 4, fill=color, width=2, capstyle=tk.ROUND)


def _draw_open(canvas, cx, cy, p):
    # Folder back
    _round_rect(canvas, cx - 9, cy - 4, cx + 9, cy + 8, r=2, fill=p["fill"], outline=p["muted"], width=1)
    # Tab
    canvas.create_polygon(
        cx - 9, cy - 4,
        cx - 9, cy - 7,
        cx - 2, cy - 7,
        cx + 1, cy - 4,
        fill=p["fill2"], outline=p["muted"], width=1,
    )
    # Folder front (open)
    canvas.create_polygon(
        cx - 9, cy + 1,
        cx - 5, cy - 2,
        cx + 9, cy - 2,
        cx + 6, cy + 8,
        cx - 9, cy + 8,
        fill=p["accent"], outline="",
    )
    canvas.create_line(cx - 9, cy + 1, cx - 5, cy - 2, cx + 9, cy - 2, fill=p["muted"])


def _draw_save(canvas, cx, cy, p):
    # Floppy body
    _round_rect(canvas, cx - 8, cy - 8, cx + 8, cy + 8, r=2, fill=p["fill"], outline=p["muted"], width=1)
    # Metal shutter
    canvas.create_rectangle(cx - 4, cy - 8, cx + 4, cy - 2, fill=p["paper"], outline=p["muted"], width=1)
    canvas.create_rectangle(cx + 1, cy - 7, cx + 3, cy - 3, fill=p["accent"], outline="")
    # Label area
    canvas.create_rectangle(cx - 5, cy + 1, cx + 5, cy + 7, fill=p["paper"], outline=p["muted"], width=1)
    canvas.create_line(cx - 3, cy + 3, cx + 3, cy + 3, fill=p["ink"])
    canvas.create_line(cx - 3, cy + 5, cx + 1, cy + 5, fill=p["muted"])


def _draw_page(canvas, cx, cy, p):
    # Fit-page / frame
    _round_rect(canvas, cx - 9, cy - 8, cx + 9, cy + 8, r=2, fill=p["fill"], outline=p["muted"], width=1)
    # Inner page
    canvas.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5, fill=p["paper"], outline=p["accent"], width=1)
    # Corner arrows suggesting fit
    canvas.create_line(cx - 8, cy - 3, cx - 8, cy - 7, cx - 4, cy - 7, fill=p["accent"], width=1)
    canvas.create_line(cx + 8, cy + 3, cx + 8, cy + 7, cx + 4, cy + 7, fill=p["accent"], width=1)


def _draw_thumbnail(canvas, cx, cy, p):
    # 2x2 grid of page thumbs
    positions = [(-6, -5), (2, -5), (-6, 3), (2, 3)]
    for i, (ox, oy) in enumerate(positions):
        fill = p["accent"] if i == 0 else p["fill"]
        canvas.create_rectangle(cx + ox, cy + oy, cx + ox + 6, cy + oy + 6, fill=fill, outline=p["muted"], width=1)
        if i == 0:
            canvas.create_line(cx + ox + 1, cy + oy + 2, cx + ox + 5, cy + oy + 2, fill=p["paper"])
            canvas.create_line(cx + ox + 1, cy + oy + 4, cx + ox + 4, cy + oy + 4, fill=p["paper"])


def _draw_panel(canvas, cx, cy, p):
    _round_rect(canvas, cx - 9, cy - 8, cx + 9, cy + 8, r=2, fill=p["fill"], outline=p["muted"], width=1)
    # Right inspector panel highlighted
    canvas.create_rectangle(cx + 2, cy - 8, cx + 9, cy + 8, fill=p["accent"], outline="")
    canvas.create_line(cx + 2, cy - 8, cx + 2, cy + 8, fill=p["muted"])
    # Content dots on left
    for y in (-3, 0, 3):
        canvas.create_line(cx - 6, cy + y, cx, cy + y, fill=p["paper"], width=1)


def _draw_print(canvas, cx, cy, p):
    # Paper top
    canvas.create_rectangle(cx - 5, cy - 9, cx + 5, cy - 2, fill=p["paper"], outline=p["muted"], width=1)
    # Printer body
    _round_rect(canvas, cx - 9, cy - 3, cx + 9, cy + 6, r=2, fill=p["fill2"], outline=p["muted"], width=1)
    canvas.create_oval(cx + 5, cy - 1, cx + 7, cy + 1, fill=p["accent"], outline="")
    # Output tray / paper
    canvas.create_rectangle(cx - 5, cy + 3, cx + 5, cy + 9, fill=p["paper"], outline=p["muted"], width=1)
    canvas.create_line(cx - 3, cy + 5, cx + 3, cy + 5, fill=p["ink"])
    canvas.create_line(cx - 3, cy + 7, cx + 1, cy + 7, fill=p["muted"])


def _draw_add(canvas, cx, cy, p):
    canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=p["fill"], outline=p["accent"], width=1)
    canvas.create_line(cx - 5, cy, cx + 5, cy, fill=p["accent"], width=2, capstyle=tk.ROUND)
    canvas.create_line(cx, cy - 5, cx, cy + 5, fill=p["accent"], width=2, capstyle=tk.ROUND)


def _draw_swap(canvas, cx, cy, p):
    # Refresh / circular arrows
    canvas.create_arc(cx - 8, cy - 8, cx + 8, cy + 8, start=40, extent=200, style=tk.ARC, outline=p["accent"], width=2)
    canvas.create_arc(cx - 8, cy - 8, cx + 8, cy + 8, start=220, extent=200, style=tk.ARC, outline=p["fg"], width=2)
    canvas.create_polygon(cx + 5, cy - 9, cx + 9, cy - 5, cx + 4, cy - 4, fill=p["accent"], outline="")
    canvas.create_polygon(cx - 5, cy + 9, cx - 9, cy + 5, cx - 4, cy + 4, fill=p["fg"], outline="")


def _draw_cut(canvas, cx, cy, p):
    # Modern scissors / delete
    color = p["danger"]
    canvas.create_oval(cx - 9, cy + 2, cx - 3, cy + 8, outline=color, width=1)
    canvas.create_oval(cx + 3, cy + 2, cx + 9, cy + 8, outline=color, width=1)
    canvas.create_line(cx - 5, cy + 3, cx + 7, cy - 8, fill=color, width=2, capstyle=tk.ROUND)
    canvas.create_line(cx + 5, cy + 3, cx - 7, cy - 8, fill=color, width=2, capstyle=tk.ROUND)
    canvas.create_line(cx - 1, cy - 1, cx + 1, cy - 1, fill=p["muted"], width=1)


def _draw_up(canvas, cx, cy, p):
    canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=p["fill"], outline=p["muted"], width=1)
    canvas.create_polygon(cx, cy - 6, cx - 6, cy + 2, cx + 6, cy + 2, fill=p["accent"], outline="")
    canvas.create_rectangle(cx - 2, cy + 1, cx + 2, cy + 6, fill=p["accent"], outline="")


def _draw_down(canvas, cx, cy, p):
    canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=p["fill"], outline=p["muted"], width=1)
    canvas.create_polygon(cx, cy + 6, cx - 6, cy - 2, cx + 6, cy - 2, fill=p["accent"], outline="")
    canvas.create_rectangle(cx - 2, cy - 6, cx + 2, cy - 1, fill=p["accent"], outline="")


def _draw_select(canvas, cx, cy, p):
    # Cursor pointer
    canvas.create_polygon(
        cx - 6, cy - 9,
        cx - 6, cy + 6,
        cx - 2, cy + 2,
        cx + 1, cy + 9,
        cx + 4, cy + 8,
        cx + 1, cy + 1,
        cx + 7, cy + 1,
        fill=p["paper"],
        outline=p["ink"],
        width=1,
    )
    # Accent tip
    canvas.create_polygon(cx - 6, cy - 9, cx - 6, cy - 2, cx - 1, cy - 4, fill=p["accent"], outline="")


def _draw_text(canvas, cx, cy, p):
    # Text tool "T" in badge
    _round_rect(canvas, cx - 9, cy - 9, cx + 9, cy + 9, r=3, fill=p["fill"], outline=p["muted"], width=1)
    canvas.create_line(cx - 5, cy - 5, cx + 5, cy - 5, fill=p["accent"], width=2, capstyle=tk.ROUND)
    canvas.create_line(cx, cy - 5, cx, cy + 6, fill=p["paper"], width=2, capstyle=tk.ROUND)
    canvas.create_line(cx - 3, cy + 6, cx + 3, cy + 6, fill=p["paper"], width=1)


def _draw_magnifier_base(canvas, cx, cy, p, lens_fill: str | None = None):
    """Shared magnifying-glass shell used by zoom + / − icons."""
    # Lens body (slightly up-left so handle has room)
    lx, ly = cx - 2, cy - 2
    canvas.create_oval(lx - 8, ly - 8, lx + 7, ly + 7, fill=lens_fill or p["fill"], outline=p["fg"], width=2)
    # Inner ring for depth
    canvas.create_oval(lx - 6, ly - 6, lx + 5, ly + 5, outline=p["muted"], width=1)
    # Handle
    canvas.create_line(lx + 5, ly + 5, cx + 9, cy + 9, fill=p["fg"], width=3, capstyle=tk.ROUND)
    canvas.create_line(lx + 5.5, ly + 5.5, cx + 8.5, cy + 8.5, fill=p["accent"], width=1, capstyle=tk.ROUND)
    return lx, ly


def _draw_minus(canvas, cx, cy, p):
    # Zoom out: magnifier with bold minus
    lx, ly = _draw_magnifier_base(canvas, cx, cy, p, lens_fill="#2a3038")
    canvas.create_line(lx - 4, ly, lx + 3, ly, fill=p["paper"], width=3, capstyle=tk.ROUND)
    canvas.create_line(lx - 3.5, ly, lx + 2.5, ly, fill=p["accent"], width=2, capstyle=tk.ROUND)


def _draw_plus(canvas, cx, cy, p):
    # Zoom in: magnifier with bold plus
    lx, ly = _draw_magnifier_base(canvas, cx, cy, p, lens_fill="#1e3a2c")
    canvas.create_line(lx - 4, ly, lx + 3, ly, fill=p["paper"], width=3, capstyle=tk.ROUND)
    canvas.create_line(lx - 0.5, ly - 3.5, lx - 0.5, ly + 3.5, fill=p["paper"], width=3, capstyle=tk.ROUND)
    canvas.create_line(lx - 3.5, ly, lx + 2.5, ly, fill=p["accent"], width=2, capstyle=tk.ROUND)
    canvas.create_line(lx - 0.5, ly - 3, lx - 0.5, ly + 3, fill=p["accent"], width=2, capstyle=tk.ROUND)


def _draw_pen(canvas, cx, cy, p):
    # Apply / check-pen hybrid (check is clearer for "apply")
    canvas.create_oval(cx - 10, cy - 10, cx + 10, cy + 10, fill=p["fill"], outline=p["accent"], width=1)
    canvas.create_line(cx - 5, cy + 1, cx - 1, cy + 5, fill=p["accent"], width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
    canvas.create_line(cx - 1, cy + 5, cx + 6, cy - 4, fill=p["accent"], width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)


def _draw_crop(canvas, cx, cy, p):
    # Fit view / maximize
    _round_rect(canvas, cx - 8, cy - 7, cx + 8, cy + 7, r=1, fill="", outline=p["muted"], width=1)
    # Corner brackets
    canvas.create_line(cx - 8, cy - 3, cx - 8, cy - 7, cx - 3, cy - 7, fill=p["accent"], width=2)
    canvas.create_line(cx + 8, cy - 3, cx + 8, cy - 7, cx + 3, cy - 7, fill=p["accent"], width=2)
    canvas.create_line(cx - 8, cy + 3, cx - 8, cy + 7, cx - 3, cy + 7, fill=p["accent"], width=2)
    canvas.create_line(cx + 8, cy + 3, cx + 8, cy + 7, cx + 3, cy + 7, fill=p["accent"], width=2)


def _draw_undo(canvas, cx, cy, p):
    """
    Restore / reset block: document page + clear counterclockwise arrow.
    Much more readable than a thin arc alone.
    """
    # Soft badge
    canvas.create_oval(cx - 11, cy - 11, cx + 11, cy + 11, fill=p["fill"], outline=p["muted"], width=1)

    # Mini document (left) — “đoạn gốc”
    canvas.create_rectangle(cx - 8, cy - 5, cx - 1, cy + 6, fill=p["paper"], outline=p["muted"], width=1)
    canvas.create_line(cx - 6, cy - 2, cx - 3, cy - 2, fill=p["ink"])
    canvas.create_line(cx - 6, cy + 0.5, cx - 3, cy + 0.5, fill=p["muted"])
    canvas.create_line(cx - 6, cy + 3, cx - 4, cy + 3, fill=p["muted"])

    # Bold restore arrow (right side, counter-clockwise into the doc)
    # Arc
    canvas.create_arc(
        cx - 3, cy - 7, cx + 9, cy + 7,
        start=40,
        extent=230,
        style=tk.ARC,
        outline=p["paper"],
        width=3,
    )
    canvas.create_arc(
        cx - 3, cy - 7, cx + 9, cy + 7,
        start=40,
        extent=230,
        style=tk.ARC,
        outline=p["accent"],
        width=2,
    )
    # Solid arrow head pointing into the document
    canvas.create_polygon(
        cx - 2, cy - 1,
        cx + 4, cy - 7,
        cx + 5, cy - 1,
        fill=p["accent"],
        outline=p["paper"],
        width=1,
    )


def _draw_search(canvas, cx, cy, p):
    canvas.create_oval(cx - 8, cy - 8, cx + 3, cy + 3, outline=p["fg"], width=2)
    canvas.create_oval(cx - 6, cy - 6, cx + 1, cy + 1, outline=p["accent"], width=1)
    canvas.create_line(cx + 2, cy + 2, cx + 8, cy + 8, fill=p["accent"], width=2, capstyle=tk.ROUND)


def _draw_fallback(canvas, cx, cy, p):
    canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=p["muted"], width=1)
    canvas.create_text(cx, cy, text="?", fill=p["fg"], font=("Segoe UI", 10, "bold"))


ICON_DRAWERS = {
    "document": _draw_document,
    "close": _draw_close,
    "open": _draw_open,
    "save": _draw_save,
    "page": _draw_page,
    "thumbnail": _draw_thumbnail,
    "panel": _draw_panel,
    "print": _draw_print,
    "add": _draw_add,
    "swap": _draw_swap,
    "cut": _draw_cut,
    "up": _draw_up,
    "down": _draw_down,
    "select": _draw_select,
    "text": _draw_text,
    "minus": _draw_minus,
    "plus": _draw_plus,
    "pen": _draw_pen,
    "crop": _draw_crop,
    "undo": _draw_undo,
    "search": _draw_search,
}


def project_root() -> Path:
    """Project root (source tree) or PyInstaller extract dir."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def app_icon_path() -> Path | None:
    """Locate pdftool.ico for window / taskbar branding."""
    candidates = [
        project_root() / "assets" / "pdftool.ico",
        Path(__file__).resolve().parent.parent / "assets" / "pdftool.ico",
        Path(sys.executable).resolve().parent / "assets" / "pdftool.ico",
        Path(sys.executable).resolve().parent / "pdftool.ico",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def make_app_icon_photo(size: int = 32) -> tk.PhotoImage:
    """
    Load a PhotoImage for iconphoto().
    Prefers assets/pdftool_*.png / pdftool.png; falls back to a drawn icon.
    """
    root = project_root()
    for name in (f"pdftool_{size}.png", "pdftool_32.png", "pdftool.png"):
        path = root / "assets" / name
        if path.is_file():
            try:
                return tk.PhotoImage(file=str(path))
            except tk.TclError:
                pass

    # Fallback: simple programmatic icon (no external file).
    w = h = size
    img = tk.PhotoImage(width=w, height=h)
    scale = size / 32.0
    for y in range(h):
        line = []
        for x in range(w):
            sx, sy = x / scale, y / scale
            r, g, b = 28, 30, 34
            if 7 <= sx <= 25 and 4 <= sy <= 28:
                r, g, b = 245, 246, 248
            if 18 <= sx <= 25 and 4 <= sy <= 10 and (sx + sy) >= 28:
                r, g, b = 55, 58, 64
            if 9 <= sx <= 22 and sy in (10, 13, 16):
                r, g, b = 32, 182, 90
            if 9 <= sx <= 16 and 22 <= sy <= 26:
                r, g, b = 32, 182, 90
            line.append(f"#{r:02x}{g:02x}{b:02x}")
        img.put("{" + " ".join(line) + "}", to=(0, y))
    return img
