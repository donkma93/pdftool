import copy
import json
import os
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

import fitz

from .font_manager import available_font_files, find_unicode_font
from .geometry import (
    constrain_moved_rect_to_page,
    constrain_rect_to_page,
    resize_handle_points,
)
from .icons import draw_icon, make_app_icon_photo
from .models import TextBlock, TextStyleSpan
from .pdf_engine import (
    apply_blocks_to_pdf,
    block_has_changes,
    changed_blocks,
    expanded_text_rect,
    extract_text_blocks,
    padded_rect,
    styled_wrapped_lines,
)
from .text_layout import fit_font_size, required_text_height, wrap_text_for_canvas, wrap_text_for_pdf
from .update_checker import download_latest_installer, is_newer_version, latest_github_tag, open_downloaded_installer, open_latest_release
from .version import (
    APP_AUTHOR,
    APP_COPYRIGHT,
    APP_DEVELOPER_LINE,
    APP_TITLE,
    APP_VERSION,
)


class ToolTip:
    """Lightweight hover tooltip for toolbar and panel controls."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        if not self.text:
            return
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            background="#1a1a1a",
            foreground="#f2f2f2",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
        ).pack()
        self._tip = tip

    def _hide(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except (tk.TclError, ValueError):
                pass
            self._after_id = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class PdfParagraphEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION} — {APP_COPYRIGHT}")
        self.geometry("1280x820")
        self.minsize(1080, 700)

        self.pdf_path: str | None = None
        self.original_pdf_path: str | None = None
        self.working_pdf_path: str | None = None
        self.doc: fitz.Document | None = None
        self.blocks: list[TextBlock] = []
        self.selected_block_id: int | None = None
        self.current_page_index = 0
        self.zoom = 1.35
        self.page_photo: tk.PhotoImage | None = None
        self.page_canvas_item: int | None = None
        self.page_margin = 24
        self.page_origin_x = 24
        self.page_origin_y = 24
        self._last_canvas_size: tuple[int, int] = (0, 0)
        self._recenter_job = None
        self.insert_mode = False
        self.resize_state: dict | None = None
        self.move_state: dict | None = None
        self.recent_files: list[str] = self.load_recent_files()
        self.sidebar_visible = True
        self.inspector_visible = True
        self.sidebar_tab = "pages"  # pages | outlines
        self.tool_buttons: dict[str, tk.Canvas] = {}
        self.find_visible = False
        self.find_query = ""
        self.find_matches: list[tuple[int, int]] = []
        self.find_index = -1
        self.inplace_block_id: int | None = None
        self.inplace_window_id: int | None = None
        self.inplace_text: tk.Text | None = None
        self._inplace_committing = False
        self._nudge_render_job = None
        self.thumbnail_images: list[tk.PhotoImage] = []
        self.thumbnail_items: list[dict] = []
        self.outline_items: list[dict] = []
        self.structure_dirty = False
        self.thumb_max_width = 132
        self.font_files = available_font_files()
        self.pdf_font_path = self.font_files.get("Arial") or find_unicode_font(self.font_files)

        self._build_ui()
        self._set_app_icon()
        self.show_welcome()
        self.update_status_bar()
        self._try_enable_file_drop()

    def _set_app_icon(self):
        try:
            self._app_icon = make_app_icon_photo(32)
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            self._app_icon = None

    def update_editor_style(self, _event=None):
        font_family = self.font_var.get() or "Arial"
        color = self.color_var.get() or "#000000"
        font_size = self.get_selected_font_size(default=11)
        self.editor.configure(font=(font_family, max(6, min(72, int(font_size))), "normal"), foreground=self.editor_display_color(color))
        self.configure_editor_style_tags()
        self.color_preview.configure(bg=color)

    def editor_display_color(self, color: str) -> str:
        try:
            value = color.lstrip("#")
            red = int(value[0:2], 16)
            green = int(value[2:4], 16)
            blue = int(value[4:6], 16)
        except (ValueError, IndexError):
            return "#f0f0f0"
        luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
        return color if luminance >= 0.38 else "#f0f0f0"

    def tk_font_style(self, bold: bool | None = None, italic: bool | None = None) -> str:
        is_bold = self.bold_var.get() if bold is None else bold
        is_italic = self.italic_var.get() if italic is None else italic
        styles = []
        if is_bold:
            styles.append("bold")
        if is_italic:
            styles.append("italic")
        return " ".join(styles) or "normal"

    def configure_editor_style_tags(self):
        if not hasattr(self, "editor") or not hasattr(self, "bold_var"):
            return
        font_family = self.font_var.get() or "Arial"
        font_size = max(6, min(72, int(self.get_selected_font_size(default=11))))
        self.editor.tag_configure("style_bold", font=(font_family, font_size, "bold"))
        self.editor.tag_configure("style_italic", font=(font_family, font_size, "italic"))
        self.editor.tag_configure("style_bold_italic", font=(font_family, font_size, "bold italic"))

    def toggle_bold_selection(self):
        if self.editor_has_selection():
            self.apply_selection_style(bold=self.bold_var.get())
        self.update_editor_style()

    def toggle_italic_selection(self):
        if self.editor_has_selection():
            self.apply_selection_style(italic=self.italic_var.get())
        self.update_editor_style()

    def editor_has_selection(self) -> bool:
        try:
            self.editor.index(tk.SEL_FIRST)
            self.editor.index(tk.SEL_LAST)
            return True
        except tk.TclError:
            return False

    def apply_selection_style(self, bold: bool | None = None, italic: bool | None = None):
        try:
            start_index = self.editor.index(tk.SEL_FIRST)
            end_index = self.editor.index(tk.SEL_LAST)
        except tk.TclError:
            return
        start = self.editor_offset(start_index)
        end = self.editor_offset(end_index)
        if start >= end:
            return

        text_length = len(self.editor.get("1.0", tk.END).rstrip())
        start = max(0, min(start, text_length))
        end = max(0, min(end, text_length))
        old_styles = [self.editor_style_at_offset(offset) for offset in range(start, end)]
        self.remove_style_tags(f"1.0+{start}c", f"1.0+{end}c")

        run_start = start
        run_style: tuple[bool, bool] | None = None
        for offset, current_style in enumerate(old_styles, start=start):
            next_style = (
                current_style[0] if bold is None else bold,
                current_style[1] if italic is None else italic,
            )
            if run_style is not None and next_style != run_style:
                self.add_style_tag(run_start, offset, run_style)
                run_start = offset
            run_style = next_style
        if run_style is not None:
            self.add_style_tag(run_start, end, run_style)

    def remove_style_tags(self, start_index: str, end_index: str):
        for tag in ("style_bold", "style_italic", "style_bold_italic"):
            self.editor.tag_remove(tag, start_index, end_index)

    def add_style_tag(self, start: int, end: int, style: tuple[bool, bool]):
        if start >= end:
            return
        tag = self.style_tag_name(*style)
        if tag:
            self.editor.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")

    def style_tag_name(self, bold: bool, italic: bool) -> str | None:
        if bold and italic:
            return "style_bold_italic"
        if bold:
            return "style_bold"
        if italic:
            return "style_italic"
        return None

    def editor_style_at_offset(self, offset: int) -> tuple[bool, bool]:
        tags = set(self.editor.tag_names(f"1.0+{offset}c"))
        if "style_bold_italic" in tags:
            return True, True
        if "style_bold" in tags:
            return True, False
        if "style_italic" in tags:
            return False, True
        return False, False

    def editor_offset(self, index: str) -> int:
        count = self.editor.count("1.0", index, "chars")
        return int(count[0]) if count else 0

    def get_selected_font_size(self, default: float = 11.0) -> float:
        try:
            return max(6.0, min(72.0, float(self.font_size_var.get())))
        except (tk.TclError, ValueError, AttributeError):
            return default

    def choose_text_color(self):
        color = colorchooser.askcolor(color=self.color_var.get(), title="Chọn màu chữ")
        if not color or not color[1]:
            return
        self.color_var.set(color[1])
        self.update_editor_style()

    def check_for_updates(self):
        try:
            latest_tag = latest_github_tag()
        except Exception as exc:
            messagebox.showerror("Không thể kiểm tra cập nhật", f"Không kết nối được GitHub hoặc repo chưa sẵn sàng:\n{exc}")
            return
        if not latest_tag:
            messagebox.showinfo("Chưa có bản phát hành", f"Chưa tìm thấy tag phát hành trên GitHub.\nPhiên bản hiện tại: v{APP_VERSION}")
            return
        if is_newer_version(latest_tag, APP_VERSION):
            should_open = messagebox.askyesno(
                "Có bản cập nhật mới",
                f"Phiên bản hiện tại: v{APP_VERSION}\nPhiên bản mới nhất: {latest_tag}\n\nTải bản cài đặt mới?",
            )
            if should_open:
                try:
                    installer_path = download_latest_installer(latest_tag)
                except Exception as exc:
                    messagebox.showerror("Không thể tải bản cập nhật", f"Không tải được file cài đặt:\n{exc}")
                    open_latest_release(latest_tag)
                    return
                if installer_path is None:
                    messagebox.showinfo("Chưa có file cài đặt", "Release mới chưa có file cài đặt. Chương trình sẽ mở trang release.")
                    open_latest_release(latest_tag)
                    return
                messagebox.showinfo("Đã tải bản cập nhật", f"Đã tải file:\n{installer_path}\n\nHãy đóng PDFTOOL rồi chạy file cài đặt để cập nhật.")
                open_downloaded_installer(installer_path)
            return
        messagebox.showinfo("Đã là bản mới nhất", f"Phiên bản hiện tại: v{APP_VERSION}\nTag mới nhất trên GitHub: {latest_tag}")

    def configure_dark_theme(self):
        self.colors = {
            "bg": "#242424",
            "top": "#171717",
            "rail": "#111414",
            "panel": "#202020",
            "panel_alt": "#2b2b2b",
            "line": "#343434",
            "text": "#ededed",
            "muted": "#898989",
            "accent": "#20b65a",
            "accent_dark": "#126c3b",
            "canvas": "#3a3a3a",
            "editor": "#121212",
        }
        self.configure(bg=self.colors["bg"])
        self.option_add("*Font", ("Segoe UI", 9))
        self.option_add("*Background", self.colors["bg"])
        self.option_add("*Foreground", self.colors["text"])

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=self.colors["panel"], foreground=self.colors["text"])
        style.configure("Dark.TFrame", background=self.colors["panel"])
        style.configure("Dark.TLabel", background=self.colors["panel"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Dark.TCombobox", fieldbackground="#151515", background="#151515", foreground=self.colors["text"])
        style.configure("Dark.TSpinbox", fieldbackground="#151515", background="#151515", foreground=self.colors["text"])
        style.configure("Vertical.TScrollbar", background="#2f2f2f", troughcolor="#1a1a1a", bordercolor="#1a1a1a", arrowcolor=self.colors["text"])
        style.configure("Horizontal.TScrollbar", background="#2f2f2f", troughcolor="#1a1a1a", bordercolor="#1a1a1a", arrowcolor=self.colors["text"])

    def tool_button(
        self,
        parent,
        icon: str,
        command=None,
        active: bool = False,
        width: int = 36,
        tooltip: str | None = None,
        tool_name: str | None = None,
    ):
        height = 34
        bg = "#163528" if active else self.colors["top"]
        button = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        button.pack(side=tk.LEFT, padx=(2, 0), pady=3)
        button._icon = icon  # type: ignore[attr-defined]
        button._tool_name = tool_name  # type: ignore[attr-defined]
        button._width = width  # type: ignore[attr-defined]
        button._height = height  # type: ignore[attr-defined]
        button._active = active  # type: ignore[attr-defined]
        self.draw_toolbar_icon(button, icon, width, active)

        def run(_event=None):
            if command is not None:
                command()

        def enter(_event=None):
            is_active = bool(getattr(button, "_active", False))
            button.configure(bg="#1f6b42" if is_active else "#2a2a2e")
            self.draw_toolbar_icon(button, icon, width, is_active)

        def leave(_event=None):
            is_active = bool(getattr(button, "_active", False))
            button.configure(bg="#163528" if is_active else self.colors["top"])
            self.draw_toolbar_icon(button, icon, width, is_active)

        button.bind("<Button-1>", run)
        button.bind("<Return>", run)
        button.bind("<Enter>", enter)
        button.bind("<Leave>", leave)
        if tooltip:
            ToolTip(button, tooltip)
        if tool_name:
            self.tool_buttons[tool_name] = button
        return button

    def set_tool_button_active(self, tool_name: str, active: bool):
        button = self.tool_buttons.get(tool_name)
        if button is None:
            return
        button._active = active  # type: ignore[attr-defined]
        bg = "#163528" if active else self.colors["top"]
        button.configure(bg=bg)
        self.draw_toolbar_icon(
            button,
            button._icon,  # type: ignore[attr-defined]
            button._width,  # type: ignore[attr-defined]
            active,
        )

    def refresh_tool_button_states(self):
        self.set_tool_button_active("select", not self.insert_mode)
        self.set_tool_button_active("insert", self.insert_mode)

    def draw_toolbar_icon(self, canvas: tk.Canvas, icon: str, width: int, active: bool = False):
        height = int(getattr(canvas, "_height", 34) or 34)
        draw_icon(canvas, icon, width=width, height=height, active=active)

    def panel_button(self, parent, text: str, command, accent: bool = False):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.colors["accent_dark"] if accent else "#303030",
            fg="#ffffff",
            activebackground=self.colors["accent"] if accent else "#3d3d3d",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
            font=("Segoe UI", 9),
        )
        return button

    def toolbar_separator(self, parent):
        tk.Frame(parent, width=1, bg="#3f3f46").pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=9)

    def section_label(self, parent, text: str):
        return tk.Label(
            parent,
            text=text,
            bg=parent["bg"],
            fg=self.colors["accent"],
            font=("Segoe UI", 8, "bold"),
            anchor=tk.W,
        )

    def recent_store_path(self) -> Path:
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "PDFTOOL"
        base.mkdir(parents=True, exist_ok=True)
        return base / "recent.json"

    def load_recent_files(self) -> list[str]:
        try:
            raw = json.loads(self.recent_store_path().read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            return [path for path in raw if isinstance(path, str) and os.path.isfile(path)][:5]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []

    def save_recent_files(self):
        try:
            self.recent_store_path().write_text(
                json.dumps(self.recent_files[:5], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def update_recent_panel(self, path: str):
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:5]
        self.save_recent_files()
        self.refresh_recent_ui()

    def refresh_recent_ui(self):
        if hasattr(self, "recent_list_frame"):
            for child in self.recent_list_frame.winfo_children():
                child.destroy()
            if not self.recent_files:
                tk.Label(
                    self.recent_list_frame,
                    text="Chưa có file gần đây",
                    bg=self.colors["panel"],
                    fg=self.colors["muted"],
                    anchor=tk.W,
                ).pack(fill=tk.X)
            else:
                for path in self.recent_files:
                    self._add_recent_row(self.recent_list_frame, path, compact=True)
        if hasattr(self, "welcome_recent_frame"):
            for child in self.welcome_recent_frame.winfo_children():
                child.destroy()
            if not self.recent_files:
                tk.Label(
                    self.welcome_recent_frame,
                    text="Chưa có file gần đây — bấm Mở PDF để bắt đầu",
                    bg=self.colors["canvas"],
                    fg=self.colors["muted"],
                ).pack(anchor=tk.W)
            else:
                for path in self.recent_files:
                    self._add_recent_row(self.welcome_recent_frame, path, compact=False)

    def _add_recent_row(self, parent, path: str, compact: bool = True):
        bg = parent.cget("bg")
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill=tk.X, pady=(0, 6 if compact else 8))
        badge = tk.Label(row, text="PDF", bg="#d71920", fg="#ffffff", font=("Segoe UI", 8, "bold"), padx=5, pady=2)
        badge.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 10))
        text_col = tk.Frame(row, bg=bg)
        text_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        name = tk.Label(
            text_col,
            text=os.path.basename(path),
            bg=bg,
            fg=self.colors["text"],
            anchor=tk.W,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        name.pack(fill=tk.X)
        folder = tk.Label(
            text_col,
            text=os.path.dirname(path),
            bg=bg,
            fg=self.colors["muted"],
            anchor=tk.W,
            font=("Segoe UI", 8),
            wraplength=260 if compact else 420,
            justify=tk.LEFT,
            cursor="hand2",
        )
        folder.pack(fill=tk.X)

        def open_this(_event=None, target=path):
            self.open_pdf_path(target)

        for widget in (row, badge, text_col, name, folder):
            widget.bind("<Button-1>", open_this)

    def confirm_discard_unsaved_changes(self) -> bool:
        has_text_edits = bool(changed_blocks(self.blocks))
        if not has_text_edits and not self.structure_dirty:
            return True
        details = []
        if has_text_edits:
            details.append("sửa text")
        if self.structure_dirty:
            details.append("xoay/xóa trang")
        detail_text = ", ".join(details)
        choice = messagebox.askyesnocancel(
            "Có thay đổi chưa lưu",
            f"PDF hiện tại có thay đổi chưa lưu ({detail_text}).\n"
            "Bạn có muốn lưu thành PDF mới trước khi tiếp tục không?",
        )
        if choice is None:
            return False
        if choice:
            return self.save_pdf()
        return True

    def close_pdf(self):
        if self.doc is None:
            return
        if not self.confirm_discard_unsaved_changes():
            return
        self.doc.close()
        self._cleanup_working_pdf()
        self.pdf_path = None
        self.original_pdf_path = None
        self.doc = None
        self.structure_dirty = False
        self.blocks = []
        self.selected_block_id = None
        self.current_page_index = 0
        self.insert_mode = False
        self.resize_state = None
        self.move_state = None
        self.page_photo = None
        self.page_canvas_item = None
        self.find_matches = []
        self.find_index = -1
        self.find_query = ""
        self.destroy_inplace_editor(commit=False)
        if self.find_visible:
            self.hide_find_bar()
        self.canvas.delete("all")
        self.editor.delete("1.0", tk.END)
        self.page_label.config(text="- / -")
        self.zoom_label.config(text="Fit Page")
        self.file_label.config(text="Chưa mở PDF")
        self.block_info.config(text="Chưa chọn đoạn")
        self.refresh_page_thumbnails()
        self.refresh_outlines()
        self.refresh_tool_button_states()
        self.show_welcome()
        self.update_status_bar("Đã đóng PDF")

    def open_pdf_location(self):
        if not self.pdf_path:
            self.open_pdf()
            return
        try:
            os.startfile(os.path.dirname(self.pdf_path))
        except OSError as exc:
            messagebox.showerror("Không thể mở thư mục", str(exc))

    def toggle_sidebar(self):
        if self.sidebar_visible:
            self.sidebar.pack_forget()
        else:
            self.sidebar.pack(side=tk.LEFT, fill=tk.Y, before=self.viewer_frame)
        self.sidebar_visible = not self.sidebar_visible

    def _build_pages_sidebar(self):
        tab_row = tk.Frame(self.sidebar, bg=self.colors["rail"])
        tab_row.pack(fill=tk.X, padx=8, pady=(12, 4))
        self.pages_tab_btn = tk.Label(
            tab_row,
            text="PAGES",
            bg=self.colors["rail"],
            fg=self.colors["accent"],
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            padx=4,
        )
        self.pages_tab_btn.pack(side=tk.LEFT)
        self.outlines_tab_btn = tk.Label(
            tab_row,
            text="OUTLINES",
            bg=self.colors["rail"],
            fg="#555555",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            padx=4,
        )
        self.outlines_tab_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.pages_tab_btn.bind("<Button-1>", lambda _e: self.set_sidebar_tab("pages"))
        self.outlines_tab_btn.bind("<Button-1>", lambda _e: self.set_sidebar_tab("outlines"))

        self.pages_panel = tk.Frame(self.sidebar, bg=self.colors["rail"])
        self.outlines_panel = tk.Frame(self.sidebar, bg=self.colors["rail"])

        # ---- Pages panel ----
        header = tk.Frame(self.pages_panel, bg=self.colors["rail"])
        header.pack(fill=tk.X, padx=10, pady=(4, 6))
        self.sidebar_page_count = tk.Label(
            header,
            text="0 trang",
            bg=self.colors["rail"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        )
        self.sidebar_page_count.pack(side=tk.RIGHT)
        tk.Label(
            header,
            text="Click chọn · chuột phải xoay/xóa",
            bg=self.colors["rail"],
            fg="#666666",
            font=("Segoe UI", 7),
        ).pack(side=tk.LEFT)

        nav = tk.Frame(self.pages_panel, bg=self.colors["rail"])
        nav.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.sidebar_page_label = tk.Label(
            nav,
            text="— / —",
            bg=self.colors["rail"],
            fg=self.colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        self.sidebar_page_label.pack(side=tk.LEFT)
        self.panel_button(nav, "↑", self.previous_page).pack(side=tk.RIGHT, padx=(4, 0))
        self.panel_button(nav, "↓", self.next_page).pack(side=tk.RIGHT)

        list_shell = tk.Frame(self.pages_panel, bg=self.colors["rail"])
        list_shell.pack(fill=tk.BOTH, expand=True, padx=(6, 2), pady=(0, 8))

        self.thumb_canvas = tk.Canvas(
            list_shell,
            bg=self.colors["rail"],
            highlightthickness=0,
            bd=0,
        )
        self.thumb_scrollbar = ttk.Scrollbar(list_shell, orient=tk.VERTICAL, command=self.thumb_canvas.yview)
        self.thumb_canvas.configure(yscrollcommand=self.thumb_scrollbar.set)
        self.thumb_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.thumb_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.thumb_list = tk.Frame(self.thumb_canvas, bg=self.colors["rail"])
        self.thumb_window_id = self.thumb_canvas.create_window((0, 0), window=self.thumb_list, anchor=tk.NW)

        def _on_list_configure(_event=None):
            self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all"))
            canvas_width = max(1, self.thumb_canvas.winfo_width())
            self.thumb_canvas.itemconfigure(self.thumb_window_id, width=canvas_width)

        def _on_canvas_configure(event):
            self.thumb_canvas.itemconfigure(self.thumb_window_id, width=max(1, event.width))

        self.thumb_list.bind("<Configure>", _on_list_configure)
        self.thumb_canvas.bind("<Configure>", _on_canvas_configure)
        self.thumb_canvas.bind("<Enter>", lambda _e: self.thumb_canvas.focus_set())
        self.thumb_canvas.bind("<MouseWheel>", self._on_thumb_mousewheel)
        self.thumb_list.bind("<MouseWheel>", self._on_thumb_mousewheel)
        self.thumb_canvas.bind("<Button-4>", lambda _e: self.thumb_canvas.yview_scroll(-1, "units"))
        self.thumb_canvas.bind("<Button-5>", lambda _e: self.thumb_canvas.yview_scroll(1, "units"))

        self.thumb_empty_label = tk.Label(
            self.thumb_list,
            text="Mở PDF để xem\ncác trang",
            bg=self.colors["rail"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            justify=tk.CENTER,
            pady=24,
        )
        self.thumb_empty_label.pack(fill=tk.X, padx=8, pady=12)

        # ---- Outlines panel ----
        outline_header = tk.Frame(self.outlines_panel, bg=self.colors["rail"])
        outline_header.pack(fill=tk.X, padx=10, pady=(4, 6))
        self.outline_count_label = tk.Label(
            outline_header,
            text="0 mục",
            bg=self.colors["rail"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        )
        self.outline_count_label.pack(side=tk.RIGHT)
        tk.Label(
            outline_header,
            text="Bookmark / mục lục",
            bg=self.colors["rail"],
            fg="#666666",
            font=("Segoe UI", 7),
        ).pack(side=tk.LEFT)

        outline_shell = tk.Frame(self.outlines_panel, bg=self.colors["rail"])
        outline_shell.pack(fill=tk.BOTH, expand=True, padx=(6, 2), pady=(0, 8))
        self.outline_canvas = tk.Canvas(outline_shell, bg=self.colors["rail"], highlightthickness=0, bd=0)
        self.outline_scrollbar = ttk.Scrollbar(outline_shell, orient=tk.VERTICAL, command=self.outline_canvas.yview)
        self.outline_canvas.configure(yscrollcommand=self.outline_scrollbar.set)
        self.outline_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.outline_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.outline_list = tk.Frame(self.outline_canvas, bg=self.colors["rail"])
        self.outline_window_id = self.outline_canvas.create_window((0, 0), window=self.outline_list, anchor=tk.NW)

        def _on_outline_list_configure(_event=None):
            self.outline_canvas.configure(scrollregion=self.outline_canvas.bbox("all"))
            self.outline_canvas.itemconfigure(self.outline_window_id, width=max(1, self.outline_canvas.winfo_width()))

        def _on_outline_canvas_configure(event):
            self.outline_canvas.itemconfigure(self.outline_window_id, width=max(1, event.width))

        self.outline_list.bind("<Configure>", _on_outline_list_configure)
        self.outline_canvas.bind("<Configure>", _on_outline_canvas_configure)
        self.outline_canvas.bind("<MouseWheel>", self._on_outline_mousewheel)
        self.outline_list.bind("<MouseWheel>", self._on_outline_mousewheel)

        self.outline_empty_label = tk.Label(
            self.outline_list,
            text="Không có outline\ntrong PDF này",
            bg=self.colors["rail"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            justify=tk.CENTER,
            pady=24,
        )
        self.outline_empty_label.pack(fill=tk.X, padx=8, pady=12)

        self.set_sidebar_tab("pages")

    def set_sidebar_tab(self, tab: str):
        tab = "outlines" if tab == "outlines" else "pages"
        self.sidebar_tab = tab
        if tab == "pages":
            self.outlines_panel.pack_forget()
            self.pages_panel.pack(fill=tk.BOTH, expand=True)
            self.pages_tab_btn.configure(fg=self.colors["accent"])
            self.outlines_tab_btn.configure(fg="#555555")
        else:
            self.pages_panel.pack_forget()
            self.outlines_panel.pack(fill=tk.BOTH, expand=True)
            self.pages_tab_btn.configure(fg="#555555")
            self.outlines_tab_btn.configure(fg=self.colors["accent"])
            self.refresh_outlines()

    def _on_outline_mousewheel(self, event):
        if not hasattr(self, "outline_canvas"):
            return
        delta = int(-1 * (event.delta / 120)) if event.delta else 0
        if delta == 0:
            return
        self.outline_canvas.yview_scroll(delta, "units")
        return "break"

    def _on_thumb_mousewheel(self, event):
        if not hasattr(self, "thumb_canvas"):
            return
        delta = int(-1 * (event.delta / 120)) if event.delta else 0
        if delta == 0:
            return
        self.thumb_canvas.yview_scroll(delta, "units")
        return "break"

    def clear_page_thumbnails(self):
        self.thumbnail_images.clear()
        self.thumbnail_items.clear()
        if not hasattr(self, "thumb_list"):
            return
        for child in self.thumb_list.winfo_children():
            child.destroy()

    def refresh_page_thumbnails(self):
        """Rebuild the full page thumbnail list for the open document."""
        if not hasattr(self, "thumb_list"):
            return
        self.clear_page_thumbnails()

        if self.doc is None or len(self.doc) == 0:
            if hasattr(self, "sidebar_page_count"):
                self.sidebar_page_count.config(text="0 trang")
            if hasattr(self, "sidebar_page_label"):
                self.sidebar_page_label.config(text="— / —")
            self.thumb_empty_label = tk.Label(
                self.thumb_list,
                text="Mở PDF để xem\ncác trang",
                bg=self.colors["rail"],
                fg=self.colors["muted"],
                font=("Segoe UI", 9),
                justify=tk.CENTER,
                pady=24,
            )
            self.thumb_empty_label.pack(fill=tk.X, padx=8, pady=12)
            self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all") or (0, 0, 0, 0))
            return

        page_count = len(self.doc)
        self.sidebar_page_count.config(text=f"{page_count} trang")

        for page_index in range(page_count):
            item = self._create_thumbnail_item(page_index)
            self.thumbnail_items.append(item)

        self.update_page_thumbnail_selection(scroll_into_view=True)
        self.thumb_list.update_idletasks()
        self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all") or (0, 0, 0, 0))

    def _create_thumbnail_item(self, page_index: int) -> dict:
        active = page_index == self.current_page_index
        border = self.colors["accent"] if active else "#3a3a3a"
        row = tk.Frame(self.thumb_list, bg=self.colors["rail"], cursor="hand2")
        row.pack(fill=tk.X, padx=6, pady=6)

        card = tk.Frame(row, bg=border, padx=2, pady=2)
        card.pack(fill=tk.X)

        inner = tk.Frame(card, bg="#2a2a2a")
        inner.pack(fill=tk.X)

        photo = self._render_page_thumbnail(page_index)
        image_label = tk.Label(inner, image=photo, bg="#2a2a2a", bd=0)
        if photo is not None:
            image_label.image = photo  # keep reference on widget too
            self.thumbnail_images.append(photo)
        else:
            image_label.configure(
                text=f"Trang {page_index + 1}",
                fg=self.colors["muted"],
                font=("Segoe UI", 8),
                width=16,
                height=8,
            )
        image_label.pack(padx=4, pady=(6, 2))

        caption = tk.Label(
            inner,
            text=f"Trang {page_index + 1}",
            bg="#2a2a2a",
            fg="#ffffff" if active else self.colors["muted"],
            font=("Segoe UI", 8, "bold" if active else "normal"),
        )
        caption.pack(pady=(0, 6))

        def open_page(_event=None, index=page_index):
            self.goto_page(index)

        def context_menu(event, index=page_index):
            self._show_page_context_menu(event, index)

        for widget in (row, card, inner, image_label, caption):
            widget.bind("<Button-1>", open_page)
            widget.bind("<Button-3>", context_menu)
            widget.bind("<MouseWheel>", self._on_thumb_mousewheel)

        return {
            "page_index": page_index,
            "row": row,
            "card": card,
            "caption": caption,
            "image_label": image_label,
            "photo": photo,
        }

    def _show_page_context_menu(self, event, page_index: int):
        if self.doc is None:
            return
        menu = tk.Menu(self, tearoff=0, bg="#2a2a2a", fg="#ededed", activebackground=self.colors["accent_dark"])
        menu.add_command(label=f"Mở trang {page_index + 1}", command=lambda: self.goto_page(page_index))
        menu.add_separator()
        menu.add_command(label="Xoay trái 90°", command=lambda: self.rotate_page(page_index, -90))
        menu.add_command(label="Xoay phải 90°", command=lambda: self.rotate_page(page_index, 90))
        menu.add_separator()
        menu.add_command(label="Xóa trang…", command=lambda: self.delete_page(page_index))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def refresh_outlines(self):
        if not hasattr(self, "outline_list"):
            return
        for child in self.outline_list.winfo_children():
            child.destroy()
        self.outline_items.clear()

        if self.doc is None:
            if hasattr(self, "outline_count_label"):
                self.outline_count_label.config(text="0 mục")
            tk.Label(
                self.outline_list,
                text="Mở PDF để xem\nmục lục",
                bg=self.colors["rail"],
                fg=self.colors["muted"],
                font=("Segoe UI", 9),
                justify=tk.CENTER,
                pady=24,
            ).pack(fill=tk.X, padx=8, pady=12)
            return

        try:
            toc = self.doc.get_toc(simple=True) or []
        except Exception:
            toc = []

        if hasattr(self, "outline_count_label"):
            self.outline_count_label.config(text=f"{len(toc)} mục")

        if not toc:
            tk.Label(
                self.outline_list,
                text="PDF không có outline\n(bookmark / mục lục)",
                bg=self.colors["rail"],
                fg=self.colors["muted"],
                font=("Segoe UI", 9),
                justify=tk.CENTER,
                pady=24,
            ).pack(fill=tk.X, padx=8, pady=12)
            self.outline_canvas.configure(scrollregion=self.outline_canvas.bbox("all") or (0, 0, 0, 0))
            return

        for entry in toc:
            try:
                level = int(entry[0]) if len(entry) > 0 else 1
                title = str(entry[1]) if len(entry) > 1 else "Không tên"
                page_one_based = int(entry[2]) if len(entry) > 2 else 1
            except (TypeError, ValueError, IndexError):
                continue
            page_index = max(0, page_one_based - 1)
            if self.doc is not None:
                page_index = min(page_index, len(self.doc) - 1)

            indent = max(0, level - 1) * 12
            row = tk.Frame(self.outline_list, bg=self.colors["rail"], cursor="hand2")
            row.pack(fill=tk.X, padx=(8 + indent, 8), pady=2)
            label = tk.Label(
                row,
                text=title if title.strip() else "(không tên)",
                bg=self.colors["rail"],
                fg=self.colors["text"],
                font=("Segoe UI", 8),
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=130,
            )
            label.pack(side=tk.LEFT, fill=tk.X, expand=True)
            page_badge = tk.Label(
                row,
                text=str(page_index + 1),
                bg=self.colors["rail"],
                fg=self.colors["muted"],
                font=("Segoe UI", 8),
            )
            page_badge.pack(side=tk.RIGHT)

            def jump(_event=None, index=page_index, title_text=title):
                self.goto_page(index)
                self.update_status_bar(f"Outline → trang {index + 1}: {title_text[:40]}")

            def hover_in(_event=None, widgets=(row, label, page_badge)):
                for w in widgets:
                    w.configure(bg="#1c2520")

            def hover_out(_event=None, widgets=(row, label, page_badge)):
                for w in widgets:
                    w.configure(bg=self.colors["rail"])

            for widget in (row, label, page_badge):
                widget.bind("<Button-1>", jump)
                widget.bind("<Enter>", hover_in)
                widget.bind("<Leave>", hover_out)
                widget.bind("<MouseWheel>", self._on_outline_mousewheel)

            self.outline_items.append({"page_index": page_index, "title": title, "row": row})

        self.outline_list.update_idletasks()
        self.outline_canvas.configure(scrollregion=self.outline_canvas.bbox("all") or (0, 0, 0, 0))

    def rotate_page(self, page_index: int, degrees: int = 90):
        if self.doc is None or page_index < 0 or page_index >= len(self.doc):
            return
        if not self.confirm_discard_unsaved_changes_for_structure("xoay trang"):
            return
        self.commit_inplace_edit()
        try:
            page = self.doc[page_index]
            page.set_rotation((page.rotation + int(degrees)) % 360)
            self._after_structure_change(focus_page=page_index, message=f"Đã xoay trang {page_index + 1} ({degrees:+d}°)")
        except Exception as exc:
            messagebox.showerror("Không xoay được trang", str(exc))

    def delete_page(self, page_index: int):
        if self.doc is None or page_index < 0 or page_index >= len(self.doc):
            return
        if len(self.doc) <= 1:
            messagebox.showinfo("Không thể xóa", "PDF chỉ còn 1 trang — không thể xóa hết.")
            return
        if not messagebox.askyesno(
            "Xóa trang",
            f"Xóa trang {page_index + 1}/{len(self.doc)}?\n\n"
            "Thao tác này ghi vào bản làm việc hiện tại (chưa lưu file cuối).",
        ):
            return
        if not self.confirm_discard_unsaved_changes_for_structure("xóa trang"):
            return
        self.commit_inplace_edit()
        try:
            self.doc.delete_page(page_index)
            focus = min(page_index, len(self.doc) - 1)
            self._after_structure_change(focus_page=focus, message=f"Đã xóa trang {page_index + 1}")
        except Exception as exc:
            messagebox.showerror("Không xóa được trang", str(exc))

    def rotate_current_page(self, degrees: int = 90):
        if self.doc is None:
            messagebox.showinfo("Chưa có PDF", "Hãy mở một file PDF trước.")
            return
        self.rotate_page(self.current_page_index, degrees)

    def delete_current_page(self):
        if self.doc is None:
            messagebox.showinfo("Chưa có PDF", "Hãy mở một file PDF trước.")
            return
        self.delete_page(self.current_page_index)

    def confirm_discard_unsaved_changes_for_structure(self, action_label: str) -> bool:
        """Text edits would be lost on re-extract after structure ops — confirm first."""
        if not changed_blocks(self.blocks):
            return True
        return messagebox.askyesno(
            "Có sửa text chưa lưu",
            f"Bạn có thay đổi text chưa lưu.\n"
            f"Nếu tiếp tục {action_label}, các sửa text sẽ bị đặt lại theo trang mới.\n\n"
            "Vẫn tiếp tục?",
        )

    def _after_structure_change(self, focus_page: int, message: str):
        """Persist page ops to a working PDF, re-extract text, refresh UI."""
        try:
            self._persist_doc_to_working_file()
        except Exception as exc:
            messagebox.showerror("Không lưu cấu trúc trang", str(exc))
            return
        self.blocks = self.extract_text_blocks(self.doc)
        self.selected_block_id = None
        self.insert_mode = False
        self.resize_state = None
        self.move_state = None
        self.find_matches = []
        self.find_index = -1
        self.editor.delete("1.0", tk.END)
        self.block_info.config(text="Chưa chọn đoạn")
        if hasattr(self, "metrics_label"):
            self.metrics_label.config(text="Chọn một đoạn trên PDF để xem thông số gốc.")
        self.current_page_index = max(0, min(focus_page, len(self.doc) - 1))
        self.structure_dirty = True
        self.refresh_page_thumbnails()
        self.refresh_outlines()
        self.refresh_tool_button_states()
        self.render_current_page()
        self.update_status_bar(message)

    def _persist_doc_to_working_file(self):
        if self.doc is None:
            return
        fd, temp_path = tempfile.mkstemp(suffix=".pdf", prefix="pdftool_work_")
        os.close(fd)
        try:
            self.doc.save(temp_path, garbage=4, deflate=True)
        except Exception:
            self.doc.save(temp_path, garbage=4, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
        self.doc.close()
        self.doc = fitz.open(temp_path)
        old_working = self.working_pdf_path
        self.working_pdf_path = temp_path
        self.pdf_path = temp_path
        if old_working and old_working != temp_path and os.path.isfile(old_working):
            try:
                os.remove(old_working)
            except OSError:
                pass

    def _cleanup_working_pdf(self):
        path = self.working_pdf_path
        self.working_pdf_path = None
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _render_page_thumbnail(self, page_index: int) -> tk.PhotoImage | None:
        if self.doc is None or page_index < 0 or page_index >= len(self.doc):
            return None
        try:
            page = self.doc[page_index]
            page_width = max(1.0, float(page.rect.width))
            zoom = self.thumb_max_width / page_width
            # Cap height so very tall pages stay usable in the rail.
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            # Tk PhotoImage can struggle with huge images; downscale if needed.
            if pix.height > 220:
                scale = 220 / pix.height
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom * scale, zoom * scale), alpha=False)
            return tk.PhotoImage(data=pix.tobytes("ppm"))
        except Exception:
            return None

    def update_page_thumbnail_selection(self, scroll_into_view: bool = True):
        if not hasattr(self, "sidebar_page_label"):
            return
        if self.doc is None or len(self.doc) == 0:
            self.sidebar_page_label.config(text="— / —")
            if hasattr(self, "sidebar_page_count"):
                self.sidebar_page_count.config(text="0 trang")
            return

        total = len(self.doc)
        current = self.current_page_index + 1
        self.sidebar_page_label.config(text=f"{current} / {total}")
        if hasattr(self, "sidebar_page_count"):
            self.sidebar_page_count.config(text=f"{total} trang")

        selected_row = None
        for item in self.thumbnail_items:
            active = item["page_index"] == self.current_page_index
            border = self.colors["accent"] if active else "#3a3a3a"
            try:
                item["card"].configure(bg=border)
                item["caption"].configure(
                    fg="#ffffff" if active else self.colors["muted"],
                    font=("Segoe UI", 8, "bold" if active else "normal"),
                )
            except tk.TclError:
                continue
            if active:
                selected_row = item.get("row")

        if scroll_into_view and selected_row is not None and hasattr(self, "thumb_canvas"):
            self._scroll_thumbnail_into_view(selected_row)

    def _scroll_thumbnail_into_view(self, row: tk.Widget):
        try:
            self.thumb_list.update_idletasks()
            self.thumb_canvas.update_idletasks()
            canvas_height = max(1, self.thumb_canvas.winfo_height())
            list_height = max(1, self.thumb_list.winfo_height())
            if list_height <= canvas_height:
                return
            # Position of row relative to list top.
            y = row.winfo_y()
            row_h = max(1, row.winfo_height())
            target = (y - (canvas_height - row_h) / 2) / list_height
            self.thumb_canvas.yview_moveto(max(0.0, min(1.0, target)))
        except tk.TclError:
            pass

    def refresh_thumbnail_for_page(self, page_index: int):
        """Update one thumbnail after the page content changes."""
        if self.doc is None:
            return
        for item in self.thumbnail_items:
            if item["page_index"] != page_index:
                continue
            photo = self._render_page_thumbnail(page_index)
            if photo is None:
                return
            self.thumbnail_images.append(photo)
            try:
                item["image_label"].configure(image=photo, text="")
                item["image_label"].image = photo
                item["photo"] = photo
            except tk.TclError:
                return
            return

    def toggle_inspector(self):
        if self.inspector_visible:
            self.inspector.pack_forget()
        else:
            self.inspector.pack(side=tk.RIGHT, fill=tk.Y)
        self.inspector_visible = not self.inspector_visible

    def fit_page_to_window(self):
        if self.doc is None or len(self.doc) == 0:
            return
        self.update_idletasks()
        page_rect = self.doc[self.current_page_index].rect
        margin = self.page_margin
        available_width = max(100, self.canvas.winfo_width() - margin * 2)
        available_height = max(100, self.canvas.winfo_height() - margin * 2)
        zoom = min(available_width / max(1, page_rect.width), available_height / max(1, page_rect.height))
        self.set_zoom(max(0.5, min(3.0, zoom)))

    def print_pdf(self):
        if self.doc is None or self.pdf_path is None:
            messagebox.showinfo("Chưa có PDF", "Hãy mở một file PDF trước.")
            return
        print_path = self.pdf_path
        try:
            if changed_blocks(self.blocks):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                    print_path = temp_file.name
                out_doc = fitz.open(self.pdf_path)
                self.apply_blocks_to_pdf(out_doc)
                out_doc.save(print_path, garbage=4, deflate=True)
                out_doc.close()
            if self.send_pdf_to_printer(print_path):
                messagebox.showinfo("Đã gửi lệnh in", "PDF đã được gửi tới trình in mặc định.")
                return
        except Exception as exc:
            messagebox.showerror("Không thể chuẩn bị PDF để in", str(exc))
            return
        self.open_pdf_for_manual_print(print_path)

    def send_pdf_to_printer(self, print_path: str) -> bool:
        try:
            os.startfile(print_path, "print")
            return True
        except OSError:
            pass
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Start-Process -FilePath $args[0] -Verb Print",
            print_path,
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False

    def open_pdf_for_manual_print(self, print_path: str):
        try:
            os.startfile(print_path)
        except OSError as exc:
            messagebox.showerror("Không thể mở PDF", str(exc))
            return
        messagebox.showinfo(
            "Không in trực tiếp được",
            "Windows chưa gắn lệnh in mặc định cho file PDF.\nPDF đã được mở ra, bạn hãy bấm Ctrl+P trong trình xem PDF để in.",
        )

    def enable_select_mode(self):
        self.insert_mode = False
        self.resize_state = None
        self.move_state = None
        self.refresh_tool_button_states()
        if self.doc is not None:
            self.block_info.config(text="Chế độ chọn: click vào đoạn text trên PDF để sửa hoặc di chuyển.")
            self.draw_text_block_overlays()
            self.draw_find_highlights()
        self.update_status_bar("Chế độ chọn")

    def clear_selection(self):
        self.commit_inplace_edit()
        self.selected_block_id = None
        self.resize_state = None
        self.move_state = None
        self.editor.delete("1.0", tk.END)
        self.block_info.config(text="Chưa chọn đoạn")
        if hasattr(self, "metrics_label"):
            self.metrics_label.config(text="Chọn một đoạn trên PDF để xem thông số gốc.")
        if self.doc is not None:
            self.draw_text_block_overlays()
            self.draw_find_highlights()
        self.update_status_bar("Đã bỏ chọn")

    def _build_ui(self):
        self.configure_dark_theme()
        self._build_menubar()

        topbar = tk.Frame(self, bg=self.colors["top"], height=42)
        topbar.pack(side=tk.TOP, fill=tk.X)
        topbar.pack_propagate(False)

        self.tool_button(topbar, "document", self.open_pdf, tooltip="Mở PDF (Ctrl+O)")
        self.tool_button(topbar, "close", self.close_pdf, tooltip="Đóng PDF")
        self.tool_button(topbar, "open", self.open_pdf_location, tooltip="Mở thư mục chứa file")
        self.tool_button(topbar, "save", self.save_pdf, tooltip="Lưu PDF mới (Ctrl+S)")
        self.toolbar_separator(topbar)
        self.tool_button(topbar, "thumbnail", self.toggle_sidebar, tooltip="Ẩn/hiện sidebar trang")
        self.tool_button(topbar, "page", self.fit_page_to_window, tooltip="Vừa trang (Ctrl+0)")
        self.tool_button(topbar, "panel", self.toggle_inspector, tooltip="Ẩn/hiện panel sửa")
        self.tool_button(topbar, "print", self.print_pdf, tooltip="In (Ctrl+P)")
        self.toolbar_separator(topbar)
        self.tool_button(topbar, "add", self.enable_insert_mode, tooltip="Chèn text mới (T)", tool_name="insert_add")
        self.tool_button(topbar, "swap", self.render_current_page, tooltip="Làm mới trang")
        self.toolbar_separator(topbar)
        self.tool_button(topbar, "cut", self.delete_selected_block, tooltip="Xóa đoạn (Del)")
        self.tool_button(topbar, "up", self.previous_page, tooltip="Trang trước (←)")
        self.tool_button(topbar, "down", self.next_page, tooltip="Trang sau (→)")

        self.page_label = tk.Label(topbar, text="- / -", bg=self.colors["top"], fg=self.colors["muted"], padx=8)
        self.page_label.pack(side=tk.LEFT, pady=2)

        spacer = tk.Frame(topbar, bg=self.colors["top"])
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.tool_button(
            topbar, "select", self.enable_select_mode, active=True, width=38, tooltip="Chế độ chọn (V)", tool_name="select"
        )
        self.tool_button(
            topbar, "text", self.enable_insert_mode, width=38, tooltip="Chế độ chèn text (T)", tool_name="insert"
        )
        self.tool_button(topbar, "minus", lambda: self.change_zoom(-0.15), width=38, tooltip="Thu nhỏ / Zoom out (Ctrl+-)")
        self.tool_button(topbar, "plus", lambda: self.change_zoom(0.15), width=38, tooltip="Phóng to / Zoom in (Ctrl++)")
        self.tool_button(topbar, "pen", self.apply_text_change, tooltip="Áp dụng sửa (Ctrl+Enter)")
        self.tool_button(topbar, "crop", self.fit_page_to_window, tooltip="Vừa khung xem")
        self.tool_button(topbar, "undo", self.restore_selected_block, width=38, tooltip="Khôi phục đoạn về bản gốc")
        self.tool_button(topbar, "search", self.show_find_bar, tooltip="Tìm text (Ctrl+F)")
        self.zoom_label = tk.Label(topbar, text="Fit Page", bg="#333333", fg="#ffffff", padx=10, pady=3)
        self.zoom_label.pack(side=tk.LEFT, padx=7, pady=6)
        self.file_label = tk.Label(topbar, text="Chưa mở PDF", bg=self.colors["top"], fg=self.colors["muted"], padx=10)
        self.file_label.pack(side=tk.LEFT, pady=2)

        self.find_bar = tk.Frame(self, bg="#1c1c1c", height=36)
        self.find_bar.pack_propagate(False)
        find_inner = tk.Frame(self.find_bar, bg="#1c1c1c")
        find_inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        tk.Label(find_inner, text="Tìm:", bg="#1c1c1c", fg=self.colors["muted"]).pack(side=tk.LEFT)
        self.find_var = tk.StringVar()
        self.find_entry = tk.Entry(
            find_inner,
            textvariable=self.find_var,
            bg="#121212",
            fg=self.colors["text"],
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3a3a3a",
            highlightcolor=self.colors["accent"],
            width=36,
        )
        self.find_entry.pack(side=tk.LEFT, padx=(8, 6), ipady=3)
        self.find_entry.bind("<Return>", lambda _e: self.find_next())
        self.find_entry.bind("<Shift-Return>", lambda _e: self.find_prev())
        self.find_entry.bind("<Escape>", lambda _e: self.hide_find_bar())
        self.find_var.trace_add("write", lambda *_args: self.on_find_query_changed())
        self.panel_button(find_inner, "↑", self.find_prev).pack(side=tk.LEFT, padx=(0, 4))
        self.panel_button(find_inner, "↓", self.find_next).pack(side=tk.LEFT, padx=(0, 8))
        self.find_status = tk.Label(find_inner, text="", bg="#1c1c1c", fg=self.colors["muted"])
        self.find_status.pack(side=tk.LEFT, padx=(0, 10))
        self.panel_button(find_inner, "Đóng", self.hide_find_bar).pack(side=tk.RIGHT)

        self.body = tk.Frame(self, bg=self.colors["bg"])
        self.body.pack(fill=tk.BOTH, expand=True)

        self.sidebar = tk.Frame(self.body, bg=self.colors["rail"], width=176)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        self._build_pages_sidebar()

        self.viewer_frame = tk.Frame(self.body, bg=self.colors["canvas"])
        self.viewer_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas_shell = tk.Frame(self.viewer_frame, bg=self.colors["canvas"])
        self.welcome = tk.Frame(self.viewer_frame, bg=self.colors["canvas"])
        # Welcome/canvas_shell are packed by show_welcome()/hide_welcome().
        self._build_welcome()

        self.canvas = tk.Canvas(self.canvas_shell, bg=self.colors["canvas"], highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(self.canvas_shell, orient=tk.VERTICAL, command=self.canvas.yview)
        x_scroll = ttk.Scrollbar(self.canvas_shell, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.canvas_shell.rowconfigure(0, weight=1)
        self.canvas_shell.columnconfigure(0, weight=1)
        self.canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.canvas.bind("<Button-1>", self.on_canvas_press)
        self.canvas.bind("<Double-Button-1>", self.on_canvas_double_click)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<MouseWheel>", self.on_viewer_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self.on_viewer_shift_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel_zoom)
        self.canvas.bind("<Button-4>", self.on_viewer_button4)
        self.canvas.bind("<Button-5>", self.on_viewer_button5)
        self.canvas.bind("<Shift-Button-4>", lambda event: self._viewer_scroll_x(-1))
        self.canvas.bind("<Shift-Button-5>", lambda event: self._viewer_scroll_x(1))
        self.canvas.bind("<Control-Button-4>", lambda event: self.zoom_at_event(event, 0.15))
        self.canvas.bind("<Control-Button-5>", lambda event: self.zoom_at_event(event, -0.15))
        self.canvas.bind("<Configure>", self.on_viewer_canvas_configure)
        # Windows often sends wheel events only to the focused widget.
        self.canvas.bind("<Enter>", self._on_viewer_enter_for_scroll)
        self.canvas_shell.bind("<Enter>", self._on_viewer_enter_for_scroll)
        self.canvas_shell.bind("<MouseWheel>", self.on_viewer_mousewheel)
        self.canvas_shell.bind("<Shift-MouseWheel>", self.on_viewer_shift_mousewheel)

        self.inspector = tk.Frame(self.body, bg=self.colors["panel"], width=330)
        self.inspector.pack(side=tk.RIGHT, fill=tk.Y)
        self.inspector.pack_propagate(False)

        recent = tk.Frame(self.inspector, bg=self.colors["panel"])
        recent.pack(fill=tk.X, padx=20, pady=(22, 10))
        self.section_label(recent, "RECENT").pack(anchor=tk.W)
        self.recent_list_frame = tk.Frame(recent, bg=self.colors["panel"])
        self.recent_list_frame.pack(fill=tk.X, pady=(12, 0))

        tk.Frame(self.inspector, height=1, bg=self.colors["line"]).pack(fill=tk.X, padx=18, pady=(0, 10))

        editor_frame = tk.Frame(self.inspector, bg=self.colors["panel"])
        editor_frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))

        self.section_label(editor_frame, "EDIT").pack(anchor=tk.W, pady=(0, 8))
        self.block_info = tk.Label(
            editor_frame,
            text="Chưa chọn đoạn — double-click trên PDF để sửa tại chỗ",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            wraplength=292,
            justify=tk.LEFT,
            anchor=tk.W,
        )
        self.block_info.pack(fill=tk.X, pady=(0, 4))
        self.metrics_label = tk.Label(
            editor_frame,
            text="Chọn một đoạn trên PDF để xem thông số gốc.",
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            wraplength=292,
            justify=tk.LEFT,
            anchor=tk.W,
            padx=8,
            pady=8,
            font=("Segoe UI", 8),
        )
        self.metrics_label.pack(fill=tk.X, pady=(0, 8))

        editor_shell = tk.Frame(editor_frame, bg="#0f0f0f", highlightthickness=1, highlightbackground="#393939")
        editor_shell.pack(fill=tk.BOTH, expand=True)
        self.editor = tk.Text(
            editor_shell,
            wrap=tk.WORD,
            undo=True,
            font=("Segoe UI", 11),
            height=12,
            bg=self.colors["editor"],
            fg="#f0f0f0",
            insertbackground="#ffffff",
            selectbackground=self.colors["accent_dark"],
            relief=tk.FLAT,
            bd=8,
        )
        self.editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        editor_scroll = ttk.Scrollbar(editor_shell, orient=tk.VERTICAL, command=self.editor.yview)
        editor_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.editor.configure(yscrollcommand=editor_scroll.set)

        style_frame = tk.Frame(editor_frame, bg=self.colors["panel_alt"], padx=10, pady=10)
        style_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Label(style_frame, text="Font", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(row=0, column=0, sticky=tk.W)
        self.font_var = tk.StringVar(value="Arial")
        self.font_combo = ttk.Combobox(
            style_frame,
            textvariable=self.font_var,
            values=list(self.font_files.keys()) or ["Arial"],
            state="readonly",
            width=20,
            style="Dark.TCombobox",
        )
        self.font_combo.grid(row=0, column=1, sticky=tk.EW, padx=(8, 0))
        self.font_combo.bind("<<ComboboxSelected>>", self.update_editor_style)

        tk.Label(style_frame, text="Kiểu", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(
            row=1, column=0, sticky=tk.W, pady=(8, 0)
        )
        style_options = tk.Frame(style_frame, bg=self.colors["panel_alt"])
        style_options.grid(row=1, column=1, sticky=tk.W, padx=(8, 0), pady=(8, 0))
        self.bold_var = tk.BooleanVar(value=False)
        self.italic_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            style_options,
            text="B",
            variable=self.bold_var,
            command=self.toggle_bold_selection,
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            selectcolor="#111111",
            activebackground=self.colors["panel_alt"],
            activeforeground=self.colors["text"],
        ).pack(side=tk.LEFT)
        tk.Checkbutton(
            style_options,
            text="I",
            variable=self.italic_var,
            command=self.toggle_italic_selection,
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            selectcolor="#111111",
            activebackground=self.colors["panel_alt"],
            activeforeground=self.colors["text"],
        ).pack(side=tk.LEFT, padx=(10, 0))

        tk.Label(style_frame, text="Cỡ", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(
            row=2, column=0, sticky=tk.W, pady=(8, 0)
        )
        self.font_size_var = tk.DoubleVar(value=11.0)
        size_frame = tk.Frame(style_frame, bg=self.colors["panel_alt"])
        size_frame.grid(row=2, column=1, sticky=tk.EW, padx=(8, 0), pady=(8, 0))
        self.font_size_spin = ttk.Spinbox(
            size_frame,
            from_=6,
            to=72,
            increment=1,
            textvariable=self.font_size_var,
            width=8,
            command=self.update_editor_style,
            style="Dark.TSpinbox",
        )
        self.font_size_spin.pack(side=tk.LEFT)
        self.font_size_spin.bind("<KeyRelease>", self.update_editor_style)
        self.font_size_spin.bind("<FocusOut>", self.update_editor_style)
        tk.Label(size_frame, text="pt", bg=self.colors["panel_alt"], fg=self.colors["muted"]).pack(side=tk.LEFT, padx=(5, 0))

        tk.Label(style_frame, text="Màu", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(
            row=3, column=0, sticky=tk.W, pady=(8, 0)
        )
        self.color_var = tk.StringVar(value="#000000")
        color_row = tk.Frame(style_frame, bg=self.colors["panel_alt"])
        color_row.grid(row=3, column=1, sticky=tk.EW, padx=(8, 0), pady=(8, 0))
        self.color_preview = tk.Label(
            color_row, text="      ", bg="#000000", relief=tk.FLAT, highlightthickness=1, highlightbackground="#777777"
        )
        self.color_preview.pack(side=tk.LEFT)
        self.panel_button(color_row, "Chọn màu", self.choose_text_color).pack(side=tk.RIGHT)
        style_frame.columnconfigure(1, weight=1)

        actions = tk.Frame(editor_frame, bg=self.colors["panel"])
        actions.pack(fill=tk.X, pady=10)
        self.panel_button(actions, "Áp dụng", self.apply_text_change, accent=True).pack(side=tk.LEFT)
        self.panel_button(actions, "Xóa", self.delete_selected_block).pack(side=tk.LEFT, padx=(6, 0))
        self.panel_button(actions, "Khôi phục", self.restore_selected_block).pack(side=tk.LEFT, padx=(6, 0))

        insert_frame = tk.Frame(editor_frame, bg=self.colors["panel_alt"], padx=10, pady=10)
        insert_frame.pack(fill=tk.X)
        self.insert_button = self.panel_button(insert_frame, "Thêm nội dung", self.enable_insert_mode, accent=True)
        self.insert_button.pack(side=tk.LEFT)
        tk.Label(insert_frame, text="Rộng", bg=self.colors["panel_alt"], fg=self.colors["muted"]).pack(
            side=tk.LEFT, padx=(10, 4)
        )
        self.insert_width_var = tk.DoubleVar(value=240.0)
        self.insert_width_spin = ttk.Spinbox(
            insert_frame, from_=40, to=600, increment=10, textvariable=self.insert_width_var, width=7, style="Dark.TSpinbox"
        )
        self.insert_width_spin.pack(side=tk.LEFT)
        tk.Label(insert_frame, text="pt", bg=self.colors["panel_alt"], fg=self.colors["muted"]).pack(side=tk.LEFT, padx=(4, 0))

        self.status_bar = tk.Frame(self, bg="#141414", height=26)
        self.status_bar.pack_propagate(False)
        self.status_left = tk.Label(
            self.status_bar, text="Sẵn sàng", bg="#141414", fg=self.colors["muted"], anchor=tk.W, padx=10
        )
        self.status_left.pack(side=tk.LEFT, fill=tk.Y)
        self.status_copyright = tk.Label(
            self.status_bar,
            text=APP_COPYRIGHT,
            bg="#141414",
            fg="#6a6a6a",
            padx=10,
            font=("Segoe UI", 8),
        )
        self.status_copyright.pack(side=tk.LEFT)
        self.status_mode = tk.Label(self.status_bar, text="Chế độ: Chọn", bg="#141414", fg=self.colors["text"], padx=10)
        self.status_mode.pack(side=tk.RIGHT)
        self.status_dirty = tk.Label(self.status_bar, text="Chưa sửa", bg="#141414", fg=self.colors["muted"], padx=10)
        self.status_dirty.pack(side=tk.RIGHT)
        self.status_zoom = tk.Label(self.status_bar, text="Zoom —", bg="#141414", fg=self.colors["muted"], padx=10)
        self.status_zoom.pack(side=tk.RIGHT)
        self.status_page = tk.Label(self.status_bar, text="Trang —", bg="#141414", fg=self.colors["muted"], padx=10)
        self.status_page.pack(side=tk.RIGHT)

        # Pack status before body so the bottom strip always stays visible.
        self.body.pack_forget()
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.body.pack(fill=tk.BOTH, expand=True)

        self.refresh_recent_ui()
        self._bind_shortcuts()

    def _build_menubar(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Mở PDF…", command=self.open_pdf, accelerator="Ctrl+O")
        file_menu.add_command(label="Đóng", command=self.close_pdf)
        file_menu.add_separator()
        file_menu.add_command(label="Lưu PDF mới…", command=self.save_pdf, accelerator="Ctrl+S")
        file_menu.add_command(label="In…", command=self.print_pdf, accelerator="Ctrl+P")
        file_menu.add_separator()
        file_menu.add_command(label="Thoát", command=self.on_app_close)
        menubar.add_cascade(label="Tệp", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Hoàn tác (ô soạn)", command=self.undo_action, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Làm lại (ô soạn)", command=self.redo_action, accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Sửa tại chỗ", command=self.start_inplace_edit_selected, accelerator="F2")
        edit_menu.add_command(label="Áp dụng sửa", command=self.apply_text_change, accelerator="Ctrl+Enter")
        edit_menu.add_command(label="Xóa đoạn", command=self.delete_selected_block, accelerator="Del")
        edit_menu.add_command(label="Khôi phục đoạn", command=self.restore_selected_block)
        edit_menu.add_command(label="Bỏ chọn", command=self.clear_selection, accelerator="Esc")
        edit_menu.add_separator()
        edit_menu.add_command(label="Tìm…", command=self.show_find_bar, accelerator="Ctrl+F")
        menubar.add_cascade(label="Sửa", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Trang trước", command=self.previous_page, accelerator="←")
        view_menu.add_command(label="Trang sau", command=self.next_page, accelerator="→")
        view_menu.add_separator()
        view_menu.add_command(label="Phóng to", command=lambda: self.change_zoom(0.15), accelerator="Ctrl++")
        view_menu.add_command(label="Thu nhỏ", command=lambda: self.change_zoom(-0.15), accelerator="Ctrl+-")
        view_menu.add_command(label="Zoom 100%", command=lambda: self.set_zoom(1.0), accelerator="Ctrl+1")
        view_menu.add_command(label="Vừa trang", command=self.fit_page_to_window, accelerator="Ctrl+0")
        view_menu.add_separator()
        view_menu.add_command(label="Ẩn/hiện sidebar", command=self.toggle_sidebar)
        view_menu.add_command(label="Ẩn/hiện panel sửa", command=self.toggle_inspector)
        view_menu.add_command(label="Tab Pages", command=lambda: self.set_sidebar_tab("pages"))
        view_menu.add_command(label="Tab Outlines", command=lambda: self.set_sidebar_tab("outlines"))
        view_menu.add_separator()
        view_menu.add_command(label="Xoay trang hiện tại trái 90°", command=lambda: self.rotate_current_page(-90))
        view_menu.add_command(label="Xoay trang hiện tại phải 90°", command=lambda: self.rotate_current_page(90))
        view_menu.add_command(label="Xóa trang hiện tại…", command=self.delete_current_page)
        view_menu.add_separator()
        view_menu.add_command(label="Chế độ chọn", command=self.enable_select_mode, accelerator="V")
        view_menu.add_command(label="Chế độ chèn text", command=self.enable_insert_mode, accelerator="T")
        menubar.add_cascade(label="Xem", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Phím tắt", command=self.show_shortcuts_help)
        help_menu.add_command(label="Kiểm tra cập nhật", command=self.check_for_updates)
        help_menu.add_separator()
        help_menu.add_command(label="Giới thiệu", command=self.show_about)
        menubar.add_cascade(label="Trợ giúp", menu=help_menu)
        self.config(menu=menubar)
        self.protocol("WM_DELETE_WINDOW", self.on_app_close)

    def _build_welcome(self):
        card = tk.Frame(self.welcome, bg=self.colors["canvas"])
        card.place(relx=0.5, rely=0.45, anchor=tk.CENTER)

        logo_row = tk.Frame(card, bg=self.colors["canvas"])
        logo_row.pack(anchor=tk.W, pady=(0, 10))
        logo = tk.Canvas(logo_row, width=44, height=44, bg=self.colors["canvas"], highlightthickness=0, bd=0)
        logo.pack(side=tk.LEFT)
        draw_icon(logo, "document", width=44, height=44, active=True)
        tk.Label(
            logo_row,
            text="PDFTOOL",
            bg=self.colors["canvas"],
            fg=self.colors["accent"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 0), anchor=tk.S, pady=(0, 6))

        tk.Label(
            card,
            text=APP_TITLE,
            bg=self.colors["canvas"],
            fg=self.colors["text"],
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            card,
            text="Mở PDF, click đoạn text để sửa, rồi xuất file mới.\nKéo thả file PDF vào cửa sổ hoặc dùng Ctrl+O.",
            bg=self.colors["canvas"],
            fg=self.colors["muted"],
            justify=tk.LEFT,
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, pady=(8, 10))
        tk.Label(
            card,
            text=f"{APP_DEVELOPER_LINE}  ·  {APP_COPYRIGHT}",
            bg=self.colors["canvas"],
            fg=self.colors["accent"],
            justify=tk.LEFT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor=tk.W, pady=(0, 18))

        actions = tk.Frame(card, bg=self.colors["canvas"])
        actions.pack(anchor=tk.W, fill=tk.X)
        self.panel_button(actions, "Mở PDF…", self.open_pdf, accent=True).pack(side=tk.LEFT)
        self.panel_button(actions, "Phím tắt", self.show_shortcuts_help).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(
            card,
            text="FILE GẦN ĐÂY",
            bg=self.colors["canvas"],
            fg=self.colors["accent"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor=tk.W, pady=(28, 10))
        self.welcome_recent_frame = tk.Frame(card, bg=self.colors["canvas"])
        self.welcome_recent_frame.pack(anchor=tk.W, fill=tk.X)

    def _bind_shortcuts(self):
        self.bind_all("<Control-o>", lambda event: self._shortcut(self.open_pdf))
        self.bind_all("<Control-O>", lambda event: self._shortcut(self.open_pdf))
        self.bind_all("<Control-s>", lambda event: self._shortcut(self.save_pdf))
        self.bind_all("<Control-S>", lambda event: self._shortcut(self.save_pdf))
        self.bind_all("<Control-f>", lambda event: self._shortcut(self.show_find_bar))
        self.bind_all("<Control-F>", lambda event: self._shortcut(self.show_find_bar))
        self.bind_all("<Control-p>", lambda event: self._shortcut(self.print_pdf))
        self.bind_all("<Control-P>", lambda event: self._shortcut(self.print_pdf))
        self.bind_all("<Control-z>", self.undo_action)
        self.bind_all("<Control-Z>", self.undo_action)
        self.bind_all("<Control-y>", self.redo_action)
        self.bind_all("<Control-Y>", self.redo_action)
        self.bind_all("<Control-Return>", lambda event: self._shortcut(self.apply_text_change))
        self.bind_all("<Control-plus>", lambda event: self._shortcut(lambda: self.change_zoom(0.15)))
        self.bind_all("<Control-equal>", lambda event: self._shortcut(lambda: self.change_zoom(0.15)))
        self.bind_all("<Control-minus>", lambda event: self._shortcut(lambda: self.change_zoom(-0.15)))
        self.bind_all("<Control-0>", lambda event: self._shortcut(self.fit_page_to_window))
        self.bind_all("<Control-1>", lambda event: self._shortcut(lambda: self.set_zoom(1.0)))
        self.bind_all("<F2>", lambda event: self._shortcut(self.start_inplace_edit_selected))
        self.bind_all("<Escape>", self.on_escape)
        self.bind_all("<Delete>", self.on_delete_key)
        for key in ("Left", "Right", "Up", "Down"):
            self.bind_all(f"<{key}>", self.on_arrow_key)
            self.bind_all(f"<Shift-{key}>", self.on_arrow_key)
            self.bind_all(f"<Control-{key}>", self.on_arrow_key)
            self.bind_all(f"<Control-Shift-{key}>", self.on_arrow_key)
        self.bind_all("<KeyPress-v>", self.on_tool_key)
        self.bind_all("<KeyPress-V>", self.on_tool_key)
        self.bind_all("<KeyPress-t>", self.on_tool_key)
        self.bind_all("<KeyPress-T>", self.on_tool_key)

    def _shortcut(self, action):
        action()
        return "break"

    def _focus_is_text_input(self) -> bool:
        widget = self.focus_get()
        if widget is None:
            return False
        if widget in {self.editor, getattr(self, "find_entry", None), self.inplace_text}:
            return True
        class_name = widget.winfo_class()
        return class_name in {"Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "Spinbox"}

    def on_escape(self, _event=None):
        if self.inplace_text is not None:
            self.cancel_inplace_edit()
            return "break"
        if self.find_visible:
            self.hide_find_bar()
            return "break"
        if self.insert_mode:
            self.enable_select_mode()
            return "break"
        self.clear_selection()
        return "break"

    def on_delete_key(self, _event=None):
        if self._focus_is_text_input():
            return None
        self.delete_selected_block()
        return "break"

    def on_arrow_key(self, event=None):
        if self._focus_is_text_input():
            return None
        if self.inplace_text is not None:
            return None

        key = (event.keysym if event is not None else "") or ""
        # When a text box is selected, arrows nudge the box on the page.
        if self.selected_block_id is not None and not self.insert_mode and self.doc is not None:
            step = self._nudge_step_from_event(event)
            dx = dy = 0.0
            if key == "Left":
                dx = -step
            elif key == "Right":
                dx = step
            elif key == "Up":
                dy = -step
            elif key == "Down":
                dy = step
            else:
                return None
            self.nudge_selected_block(dx, dy)
            return "break"

        # No box selected: arrows change pages.
        if key in {"Left", "Up"}:
            self.previous_page()
            return "break"
        if key in {"Right", "Down"}:
            self.next_page()
            return "break"
        return None

    def _nudge_step_from_event(self, event) -> float:
        """Arrow nudge distance in PDF points."""
        state = int(getattr(event, "state", 0) or 0)
        shift = bool(state & 0x0001)
        control = bool(state & 0x0004)
        if control and shift:
            return 20.0
        if shift:
            return 10.0
        if control:
            return 0.5
        return 1.0

    def nudge_selected_block(self, dx: float, dy: float):
        if self.selected_block_id is None or self.doc is None:
            return
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return
        block = self.get_block(self.selected_block_id)
        if block is None or block.deleted:
            return
        if block.page_index != self.current_page_index:
            self.goto_page(block.page_index)

        base = copy.copy(block.rect)
        moved = fitz.Rect(base.x0 + dx, base.y0 + dy, base.x1 + dx, base.y1 + dy)
        block.rect = self.constrain_moved_rect_to_page(moved, block.page_index)
        block.manually_resized = True
        self.draw_text_block_overlays()
        self.draw_find_highlights()
        self.update_block_metrics(block)
        self.block_info.config(
            text=(
                f"Trang {block.page_index + 1} | Block #{block.id} | "
                f"di chuyển bằng phím → x={block.rect.x0:.1f}, y={block.rect.y0:.1f} pt"
            )
        )
        self.update_status_bar(
            f"Đã di chuyển box #{block.id} → ({block.rect.x0:.1f}, {block.rect.y0:.1f}) pt"
        )
        # Debounce full re-render so holding an arrow stays responsive.
        job = getattr(self, "_nudge_render_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._nudge_render_job = self.after(120, self._render_after_nudge)

    def _render_after_nudge(self):
        self._nudge_render_job = None
        if self.doc is None:
            return
        selected = self.selected_block_id
        self.render_current_page()
        if selected is not None and self.get_block(selected) is not None:
            # Keep selection highlight without restarting inplace edit.
            self.selected_block_id = selected
            self.draw_text_block_overlays()
            self.update_page_thumbnail_selection(scroll_into_view=False)

    def on_tool_key(self, event):
        if self._focus_is_text_input():
            return None
        key = (event.keysym or "").lower()
        if key == "v":
            self.enable_select_mode()
            return "break"
        if key == "t":
            self.enable_insert_mode()
            return "break"
        return None

    def undo_action(self, _event=None):
        widget = self.focus_get()
        if widget == getattr(self, "find_entry", None):
            return None
        if widget == self.editor:
            try:
                self.editor.edit_undo()
            except tk.TclError:
                pass
            return "break"
        if self.selected_block_id is not None:
            self.restore_selected_block()
        return "break"

    def redo_action(self, _event=None):
        try:
            self.editor.edit_redo()
        except tk.TclError:
            pass
        return "break"

    def show_shortcuts_help(self):
        messagebox.showinfo(
            "Phím tắt",
            "Ctrl+O  Mở PDF\n"
            "Ctrl+S  Lưu PDF mới\n"
            "Ctrl+F  Tìm text\n"
            "Ctrl+P  In\n"
            "F2 / Double-click  Sửa text trực tiếp trên box\n"
            "Ctrl+Enter  Áp dụng sửa (giữ format gốc)\n"
            "Ctrl+Z / Ctrl+Y  Hoàn tác / Làm lại (ô soạn)\n"
            "Ctrl++ / Ctrl+-  Zoom\n"
            "Con lăn  Cuộn dọc preview · Shift+con lăn cuộn ngang · Ctrl+con lăn zoom\n"
            "Ctrl+0  Vừa trang\n"
            "Ctrl+1  Zoom 100%\n"
            "V  Chế độ chọn\n"
            "T  Chế độ chèn text\n"
            "←↑↓→  Di chuyển box đã chọn (Shift=10pt, Ctrl=0.5pt)\n"
            "← / →  Đổi trang (khi chưa chọn box)\n"
            "Del  Xóa đoạn đã chọn\n"
            "Esc  Hủy sửa tại chỗ / đóng tìm / bỏ chọn\n\n"
            "Sidebar PAGES: click thumbnail, chuột phải xoay/xóa trang\n"
            "Sidebar OUTLINES: click mục lục để nhảy trang",
        )

    def show_about(self):
        messagebox.showinfo(
            "Giới thiệu",
            f"{APP_TITLE}\n"
            f"Phiên bản: v{APP_VERSION}\n\n"
            f"{APP_DEVELOPER_LINE}\n"
            f"{APP_COPYRIGHT}\n\n"
            "Mở PDF, sửa text trực quan, xuất file mới.\n"
            "Nhà phát triển: donpv\n"
            "Repo: donkma93/pdftool",
        )

    def on_app_close(self):
        if self.doc is not None and not self.confirm_discard_unsaved_changes():
            return
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        self.destroy()

    def show_welcome(self):
        self.canvas_shell.pack_forget()
        self.welcome.pack(fill=tk.BOTH, expand=True)
        self.refresh_recent_ui()
        self.update_status_bar("Mở một PDF để bắt đầu")

    def hide_welcome(self):
        self.welcome.pack_forget()
        self.canvas_shell.pack(fill=tk.BOTH, expand=True)

    def update_status_bar(self, message: str | None = None):
        if not hasattr(self, "status_left"):
            return
        if message is not None:
            self.status_left.config(text=message)
        elif self.doc is None:
            self.status_left.config(text="Sẵn sàng — mở PDF hoặc kéo thả file vào cửa sổ")
        elif self.selected_block_id is not None:
            self.status_left.config(text=f"Đã chọn block #{self.selected_block_id}")
        else:
            self.status_left.config(text=os.path.basename(self.pdf_path) if self.pdf_path else "PDF đã mở")

        if self.doc is None:
            self.status_page.config(text="Trang —")
            self.status_zoom.config(text="Zoom —")
        else:
            self.status_page.config(text=f"Trang {self.current_page_index + 1}/{len(self.doc)}")
            self.status_zoom.config(text=f"Zoom {int(self.zoom * 100)}%")

        dirty_count = len(changed_blocks(self.blocks)) if self.blocks else 0
        if dirty_count or self.structure_dirty:
            parts = []
            if dirty_count:
                parts.append(f"{dirty_count} đoạn")
            if self.structure_dirty:
                parts.append("cấu trúc trang")
            self.status_dirty.config(text="Đã sửa: " + ", ".join(parts), fg="#f0c674")
        else:
            self.status_dirty.config(text="Chưa sửa", fg=self.colors["muted"])

        mode = "Chèn text" if self.insert_mode else "Chọn"
        self.status_mode.config(text=f"Chế độ: {mode}")

    def show_find_bar(self):
        if self.doc is None:
            messagebox.showinfo("Chưa có PDF", "Hãy mở một file PDF trước.")
            return
        if not self.find_visible:
            self.find_bar.pack(side=tk.TOP, fill=tk.X, before=self.body)
            self.find_visible = True
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, tk.END)
        if self.find_var.get().strip():
            self.rebuild_find_matches()
            self.find_next()

    def hide_find_bar(self):
        if not self.find_visible:
            return
        self.find_bar.pack_forget()
        self.find_visible = False
        self.find_matches = []
        self.find_index = -1
        self.find_query = ""
        self.find_status.config(text="")
        if self.doc is not None:
            self.draw_text_block_overlays()
            self.draw_find_highlights()

    def on_find_query_changed(self):
        if not self.find_visible or self.doc is None:
            return
        self.rebuild_find_matches()
        if self.find_matches:
            self.find_index = -1
            self.find_status.config(text=f"0/{len(self.find_matches)} — Enter để nhảy")
        elif self.find_var.get().strip():
            self.find_status.config(text="Không tìm thấy")
        else:
            self.find_status.config(text="")
        self.draw_find_highlights()

    def rebuild_find_matches(self):
        query = self.find_var.get().strip()
        self.find_query = query
        self.find_matches = []
        self.find_index = -1
        if not query or self.doc is None:
            return
        folded = query.casefold()
        for block in self.blocks:
            text = block.original_text if block.deleted else block.text
            if folded in text.casefold():
                self.find_matches.append((block.page_index, block.id))

    def find_next(self):
        self._navigate_find(1)

    def find_prev(self):
        self._navigate_find(-1)

    def _navigate_find(self, direction: int):
        if self.doc is None:
            return
        if not self.find_visible:
            self.show_find_bar()
        query = self.find_var.get().strip()
        if not query:
            self.find_status.config(text="Nhập nội dung cần tìm")
            return
        if query != self.find_query or not self.find_matches:
            self.rebuild_find_matches()
        if not self.find_matches:
            self.find_status.config(text="Không tìm thấy")
            self.draw_find_highlights()
            return
        if self.find_index < 0:
            self.find_index = 0 if direction >= 0 else len(self.find_matches) - 1
        else:
            self.find_index = (self.find_index + direction) % len(self.find_matches)
        page_index, block_id = self.find_matches[self.find_index]
        self.current_page_index = page_index
        self.selected_block_id = None
        self.insert_mode = False
        self.refresh_tool_button_states()
        self.render_current_page()
        self.select_block(block_id)
        self.draw_find_highlights()
        self.find_status.config(text=f"{self.find_index + 1}/{len(self.find_matches)}")
        self.update_status_bar(f"Kết quả tìm {self.find_index + 1}/{len(self.find_matches)}")

    def draw_find_highlights(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("find_highlight")
        if not self.find_matches:
            return
        for index, (page_index, block_id) in enumerate(self.find_matches):
            if page_index != self.current_page_index:
                continue
            block = self.get_block(block_id)
            if block is None:
                continue
            rect = self.pdf_rect_to_canvas(self.expanded_text_rect(block))
            is_current = index == self.find_index
            self.canvas.create_rectangle(
                *rect,
                outline="#ffcc33" if is_current else "#c9a227",
                width=3 if is_current else 2,
                dash=() if is_current else (3, 2),
                tags="find_highlight",
            )

    def _try_enable_file_drop(self):
        if os.name != "nt":
            return
        try:
            import windnd

            windnd.hook_dropfiles(self, func=self._on_files_dropped)
        except Exception:
            # Optional dependency; welcome screen + Ctrl+O still work.
            pass

    def _on_files_dropped(self, files):
        paths: list[str] = []
        for item in files:
            if isinstance(item, bytes):
                for encoding in ("utf-8", "mbcs", "latin-1"):
                    try:
                        paths.append(item.decode(encoding))
                        break
                    except UnicodeDecodeError:
                        continue
            else:
                paths.append(str(item))
        for path in paths:
            if path.lower().endswith(".pdf") and os.path.isfile(path):
                self.open_pdf_path(path)
                return

    def open_pdf(self):
        if self.doc is not None and not self.confirm_discard_unsaved_changes():
            return
        path = filedialog.askopenfilename(title="Chọn file PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        self.open_pdf_path(path, skip_confirm=True)

    def open_pdf_path(self, path: str, skip_confirm: bool = False):
        if not path or not os.path.isfile(path):
            messagebox.showerror("Không tìm thấy file", f"File không tồn tại:\n{path}")
            return
        if self.doc is not None and not skip_confirm and not self.confirm_discard_unsaved_changes():
            return

        try:
            doc = fitz.open(path)
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không thể mở PDF:\n{exc}")
            return

        if self.doc is not None:
            self.doc.close()
        self._cleanup_working_pdf()
        self.original_pdf_path = path
        self.pdf_path = path
        self.doc = doc
        self.structure_dirty = False
        self.blocks = self.extract_text_blocks(doc)
        self.selected_block_id = None
        self.insert_mode = False
        self.resize_state = None
        self.move_state = None
        self.current_page_index = 0
        self.find_matches = []
        self.find_index = -1
        self.file_label.config(text=os.path.basename(path))
        self.update_recent_panel(path)
        self.editor.delete("1.0", tk.END)
        self.hide_welcome()
        self.refresh_tool_button_states()
        self.refresh_page_thumbnails()
        self.refresh_outlines()
        self.render_current_page()
        self.update_status_bar(f"Đã mở {os.path.basename(path)}")

        if not self.blocks:
            messagebox.showwarning("Không tìm thấy text", "Không trích xuất được text. PDF này có thể là file scan ảnh.")

    def extract_text_blocks(self, doc: fitz.Document) -> list[TextBlock]:
        return extract_text_blocks(doc, self.font_files)

    def render_current_page(self):
        if self.doc is None or len(self.doc) == 0:
            return
        editing_id = self.inplace_block_id
        editing_text = None
        if self.inplace_text is not None:
            editing_text = self.inplace_text.get("1.0", "end-1c")
            self.destroy_inplace_editor(commit=False)

        self.current_page_index = max(0, min(self.current_page_index, len(self.doc) - 1))
        preview_doc = None
        render_doc = self.doc
        if self.pdf_path and changed_blocks(self.blocks):
            try:
                preview_doc = fitz.open(self.pdf_path)
                self.apply_blocks_to_pdf(preview_doc)
                render_doc = preview_doc
            except Exception:
                if preview_doc is not None:
                    preview_doc.close()
                preview_doc = None
                render_doc = self.doc

        try:
            page = render_doc[self.current_page_index]
            matrix = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            self.page_photo = tk.PhotoImage(data=pix.tobytes("ppm"))
        finally:
            if preview_doc is not None:
                preview_doc.close()

        self.canvas.delete("all")
        self.update_page_origin(pix.width, pix.height)
        self.page_canvas_item = self.canvas.create_image(
            self.page_origin_x,
            self.page_origin_y,
            image=self.page_photo,
            anchor=tk.NW,
            tags=("page_image",),
        )
        self.apply_page_scrollregion(pix.width, pix.height)
        self.page_label.config(text=f"Trang {self.current_page_index + 1}/{len(self.doc)}")
        self.zoom_label.config(text=f"Zoom {int(self.zoom * 100)}%")
        self.update_page_thumbnail_selection()
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.draw_find_highlights()
        self.update_status_bar()
        if editing_id is not None and editing_text is not None:
            block = self.get_block(editing_id)
            if block is not None and block.page_index == self.current_page_index:
                self.start_inplace_edit(editing_id, initial_text=editing_text)

    def update_page_origin(self, page_width: int | float, page_height: int | float):
        """Place the page preview in the center of the visible canvas area."""
        self.update_idletasks()
        canvas_w = max(1, int(self.canvas.winfo_width()))
        canvas_h = max(1, int(self.canvas.winfo_height()))
        # winfo can return 1 before first layout; keep a sensible fallback margin.
        if canvas_w <= 10 or canvas_h <= 10:
            self.page_origin_x = float(self.page_margin)
            self.page_origin_y = float(self.page_margin)
            return
        margin = float(self.page_margin)
        self.page_origin_x = max(margin, (canvas_w - float(page_width)) / 2.0)
        self.page_origin_y = max(margin, (canvas_h - float(page_height)) / 2.0)
        self._last_canvas_size = (canvas_w, canvas_h)

    def apply_page_scrollregion(self, page_width: int | float, page_height: int | float):
        canvas_w = max(1, int(self.canvas.winfo_width()))
        canvas_h = max(1, int(self.canvas.winfo_height()))
        margin = float(self.page_margin)
        content_right = self.page_origin_x + float(page_width) + margin
        content_bottom = self.page_origin_y + float(page_height) + margin
        scroll_w = max(float(canvas_w), content_right)
        scroll_h = max(float(canvas_h), content_bottom)
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

    def on_viewer_canvas_configure(self, event=None):
        if self.doc is None or self.page_photo is None:
            return
        width = int(getattr(event, "width", self.canvas.winfo_width()) or 0)
        height = int(getattr(event, "height", self.canvas.winfo_height()) or 0)
        if width <= 10 or height <= 10:
            return
        last_w, last_h = self._last_canvas_size
        if abs(width - last_w) < 2 and abs(height - last_h) < 2:
            return
        # Debounce rapid resize events.
        if self._recenter_job is not None:
            try:
                self.after_cancel(self._recenter_job)
            except (tk.TclError, ValueError):
                pass
        self._recenter_job = self.after(40, self.recenter_page_preview)

    def recenter_page_preview(self):
        """Re-center the already-rendered page when the viewer is resized."""
        self._recenter_job = None
        if self.doc is None or self.page_photo is None or self.page_canvas_item is None:
            return
        try:
            page_w = int(self.page_photo.width())
            page_h = int(self.page_photo.height())
        except tk.TclError:
            return

        old_x, old_y = self.page_origin_x, self.page_origin_y
        editing_id = self.inplace_block_id
        editing_text = None
        if self.inplace_text is not None:
            editing_text = self.inplace_text.get("1.0", "end-1c")
            self.destroy_inplace_editor(commit=False)

        self.update_page_origin(page_w, page_h)
        if abs(old_x - self.page_origin_x) < 0.5 and abs(old_y - self.page_origin_y) < 0.5:
            self.apply_page_scrollregion(page_w, page_h)
            if editing_id is not None and editing_text is not None:
                self.start_inplace_edit(editing_id, initial_text=editing_text)
            return

        try:
            self.canvas.coords(self.page_canvas_item, self.page_origin_x, self.page_origin_y)
        except tk.TclError:
            return
        self.apply_page_scrollregion(page_w, page_h)
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.draw_find_highlights()
        if editing_id is not None and editing_text is not None:
            self.start_inplace_edit(editing_id, initial_text=editing_text)

    def draw_edited_block_preview(self):
        self.canvas.delete("edit_preview")

    def expanded_text_rect(self, block: TextBlock) -> fitz.Rect:
        return expanded_text_rect(block, self.doc)

    def required_text_height(self, text: str, font_size: float, max_width: float) -> float:
        return required_text_height(text, font_size, max_width)

    def wrap_text_for_canvas(self, text: str, font_size: int, max_width: float) -> list[str]:
        return wrap_text_for_canvas(text, font_size, max_width)

    def fit_block_font_to_rect(self, block: TextBlock, preferred_size: float | None = None) -> bool:
        if block.deleted or not block.text.strip():
            return False
        preferred = preferred_size if preferred_size is not None else block.font_size
        text_rect = fitz.Rect(block.rect.x0 + 1, block.rect.y0 + 1, block.rect.x1 - 1, block.rect.y1 - 1)
        if text_rect.width <= 1 or text_rect.height <= 1:
            fitted_size = 6.0
        else:
            fitted_size = self._fit_font_size(text_rect, block.text, preferred)
        fitted_size = max(6.0, min(preferred, fitted_size))
        if abs(fitted_size - block.font_size) < 0.05:
            return False
        block.font_size = round(fitted_size, 1)
        if block.id == self.selected_block_id:
            self.font_size_var.set(block.font_size)
            self.update_editor_style()
        return True

    def resize_preferred_font_size(self, block: TextBlock) -> float:
        if not self.resize_state:
            return block.font_size
        start_rect = self.resize_state.get("start_rect")
        start_font_size = float(self.resize_state.get("start_font_size", block.font_size))
        if not isinstance(start_rect, fitz.Rect) or start_rect.width <= 0 or start_rect.height <= 0:
            return start_font_size
        width_ratio = block.rect.width / start_rect.width
        height_ratio = block.rect.height / start_rect.height
        scale = max(width_ratio, height_ratio)
        return max(6.0, min(72.0, start_font_size * scale))

    def draw_text_block_overlays(self):
        self.canvas.delete("block_overlay")
        for block in self.blocks_on_current_page():
            preview_rect = self.expanded_text_rect(block)
            rect = self.pdf_rect_to_canvas(preview_rect)
            outline = "#d00" if block.id == self.selected_block_id else ("#159947" if block.inserted else "#1473e6")
            width = 3 if block.id == self.selected_block_id else 1
            dash = () if block.id == self.selected_block_id else (4, 3)
            self.canvas.create_rectangle(*rect, outline=outline, width=width, dash=dash, tags="block_overlay")
            if block.deleted:
                self.canvas.create_line(rect[0], rect[1], rect[2], rect[3], fill="#d00", width=2, tags="block_overlay")
                self.canvas.create_line(rect[0], rect[3], rect[2], rect[1], fill="#d00", width=2, tags="block_overlay")
            if block.id == self.selected_block_id:
                self.draw_resize_handles(rect)

    def draw_resize_handles(self, rect: tuple[float, float, float, float]):
        for _name, x, y in self.resize_handle_points(rect):
            size = 7
            self.canvas.create_rectangle(
                x - size / 2,
                y - size / 2,
                x + size / 2,
                y + size / 2,
                outline="#d00",
                fill="white",
                width=1,
                tags="block_overlay",
            )

    def resize_handle_points(self, rect: tuple[float, float, float, float]) -> list[tuple[str, float, float]]:
        return resize_handle_points(rect)

    def blocks_on_current_page(self) -> list[TextBlock]:
        return [block for block in self.blocks if block.page_index == self.current_page_index]

    def pdf_rect_to_canvas(self, rect: fitz.Rect) -> tuple[float, float, float, float]:
        return (
            self.page_origin_x + rect.x0 * self.zoom,
            self.page_origin_y + rect.y0 * self.zoom,
            self.page_origin_x + rect.x1 * self.zoom,
            self.page_origin_y + rect.y1 * self.zoom,
        )

    def canvas_point_to_pdf(self, canvas_x: float, canvas_y: float) -> fitz.Point:
        return fitz.Point((canvas_x - self.page_origin_x) / self.zoom, (canvas_y - self.page_origin_y) / self.zoom)

    def on_canvas_press(self, event):
        if self.doc is None:
            return
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        point = self.canvas_point_to_pdf(canvas_x, canvas_y)

        if self.inplace_text is not None:
            block = self.get_block(self.inplace_block_id) if self.inplace_block_id is not None else None
            if block is None or not self.expanded_text_rect(block).contains(point):
                self.commit_inplace_edit()
            else:
                return "break"

        if self.insert_mode:
            self.create_inserted_block(point)
            return

        handle = self.find_resize_handle(canvas_x, canvas_y)
        if handle:
            block = self.get_block(self.selected_block_id) if self.selected_block_id is not None else None
            if block is not None:
                start_rect = self.expanded_text_rect(block)
                self.resize_state = {
                    "block_id": block.id,
                    "handle": handle,
                    "start_rect": copy.copy(start_rect),
                    "start_font_size": block.font_size,
                    "start_x": canvas_x,
                    "start_y": canvas_y,
                }
                return "break"

        clicked = None
        for block in reversed(self.blocks_on_current_page()):
            if self.expanded_text_rect(block).contains(point):
                clicked = block
                break
        if clicked is None:
            self.clear_selection()
            return
        self.select_block(clicked.id)
        # Keep original rect until user actually moves/resizes — do not expand permanently.
        self.move_state = {
            "block_id": clicked.id,
            "start_rect": copy.copy(self.expanded_text_rect(clicked)),
            "start_x": canvas_x,
            "start_y": canvas_y,
            "moved": False,
        }
        return "break"

    def on_canvas_double_click(self, event):
        if self.doc is None or self.insert_mode:
            return "break"
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        point = self.canvas_point_to_pdf(canvas_x, canvas_y)
        clicked = None
        for block in reversed(self.blocks_on_current_page()):
            if self.expanded_text_rect(block).contains(point):
                clicked = block
                break
        if clicked is None:
            return "break"
        self.move_state = None
        self.resize_state = None
        self.select_block(clicked.id)
        self.start_inplace_edit(clicked.id)
        return "break"

    def find_resize_handle(self, canvas_x: float, canvas_y: float) -> str | None:
        if self.selected_block_id is None:
            return None
        block = self.get_block(self.selected_block_id)
        if block is None or block.page_index != self.current_page_index:
            return None
        rect = self.pdf_rect_to_canvas(self.expanded_text_rect(block))
        hit_size = 8
        for name, handle_x, handle_y in self.resize_handle_points(rect):
            if abs(canvas_x - handle_x) <= hit_size and abs(canvas_y - handle_y) <= hit_size:
                return name
        return None

    def on_canvas_drag(self, event):
        if self.doc is None:
            return
        if self.resize_state:
            return self.resize_selected_block(event)
        if self.move_state:
            return self.move_selected_block(event)

    def resize_selected_block(self, event):
        block = self.get_block(self.resize_state["block_id"])
        if block is None:
            return

        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        dx = (canvas_x - self.resize_state["start_x"]) / self.zoom
        dy = (canvas_y - self.resize_state["start_y"]) / self.zoom
        handle = self.resize_state["handle"]
        start_rect = copy.copy(self.resize_state["start_rect"])

        if "w" in handle:
            start_rect.x0 += dx
        if "e" in handle:
            start_rect.x1 += dx
        if "n" in handle:
            start_rect.y0 += dy
        if "s" in handle:
            start_rect.y1 += dy

        block.rect = self.constrain_rect_to_page(start_rect, block.page_index)
        block.manually_resized = True
        self.fit_block_font_to_rect(block, self.resize_preferred_font_size(block))
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.block_info.config(
            text=f"Trang {block.page_index + 1} | Block #{block.id} | đang kéo kích thước {block.rect.width:.0f} x {block.rect.height:.0f} pt | font ~{block.font_size:.1f}"
        )
        return "break"

    def move_selected_block(self, event):
        block = self.get_block(self.move_state["block_id"])
        if block is None:
            return

        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        dx = (canvas_x - self.move_state["start_x"]) / self.zoom
        dy = (canvas_y - self.move_state["start_y"]) / self.zoom
        if abs(dx) < 1 and abs(dy) < 1:
            return "break"

        start_rect = copy.copy(self.move_state["start_rect"])
        moved_rect = fitz.Rect(start_rect.x0 + dx, start_rect.y0 + dy, start_rect.x1 + dx, start_rect.y1 + dy)
        block.rect = self.constrain_moved_rect_to_page(moved_rect, block.page_index)
        block.manually_resized = True
        self.move_state["moved"] = True
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.block_info.config(
            text=f"Trang {block.page_index + 1} | Block #{block.id} | đang di chuyển tới x={block.rect.x0:.0f}, y={block.rect.y0:.0f} pt"
        )
        return "break"

    def on_canvas_release(self, _event):
        if self.resize_state:
            block = self.get_block(self.resize_state["block_id"])
            self.resize_state = None
            if block is not None:
                self.select_block(block.id)
                self.block_info.config(
                    text=f"Trang {block.page_index + 1} | Block #{block.id} | đã đổi kích thước {block.rect.width:.0f} x {block.rect.height:.0f} pt, chưa lưu file"
                )
                self.render_current_page()
            return "break"
        if self.move_state:
            block = self.get_block(self.move_state["block_id"])
            moved = self.move_state.get("moved", False)
            self.move_state = None
            if block is not None and moved:
                self.select_block(block.id)
                self.block_info.config(
                    text=f"Trang {block.page_index + 1} | Block #{block.id} | đã di chuyển tới x={block.rect.x0:.0f}, y={block.rect.y0:.0f} pt, chưa lưu file"
                )
                self.render_current_page()
                return "break"
        return None

    def constrain_rect_to_page(self, rect: fitz.Rect, page_index: int) -> fitz.Rect:
        if self.doc is None:
            return rect
        return constrain_rect_to_page(rect, self.doc[page_index].rect)

    def constrain_moved_rect_to_page(self, rect: fitz.Rect, page_index: int) -> fitz.Rect:
        if self.doc is None:
            return rect
        return constrain_moved_rect_to_page(rect, self.doc[page_index].rect)

    def enable_insert_mode(self):
        if self.doc is None:
            messagebox.showinfo("Chưa có PDF", "Hãy mở một file PDF trước.")
            return
        self.insert_mode = True
        self.selected_block_id = None
        self.editor.delete("1.0", tk.END)
        self.block_info.config(text="Đang chờ vị trí chèn: click vào vị trí bất kỳ trên trang PDF.")
        self.refresh_tool_button_states()
        self.draw_text_block_overlays()
        self.draw_find_highlights()
        self.update_status_bar("Chế độ chèn — click vị trí trên trang")

    def create_inserted_block(self, point: fitz.Point):
        if self.doc is None:
            return
        page = self.doc[self.current_page_index]
        width = self.get_insert_width()
        font_size = self.get_selected_font_size(default=11)
        x0 = max(0.0, min(point.x, page.rect.x1 - 20))
        y0 = max(0.0, min(point.y, page.rect.y1 - font_size - 4))
        x1 = min(page.rect.x1, x0 + width)
        y1 = min(page.rect.y1, y0 + max(font_size * 1.6, 24.0))
        block_id = self.next_block_id()
        family = self.font_var.get() or "Arial"
        color = self.color_var.get() or "#000000"
        bold = bool(self.bold_var.get())
        italic = bool(self.italic_var.get())
        block = TextBlock(
            id=block_id,
            page_index=self.current_page_index,
            rect=fitz.Rect(x0, y0, x1, y1),
            text="",
            original_text="",
            font_size=font_size,
            original_font_size=font_size,
            font_family=family,
            original_font_family=family,
            text_color=color,
            original_text_color=color,
            bold=bold,
            original_bold=bold,
            italic=italic,
            original_italic=italic,
            inserted=True,
        )
        self.blocks.append(block)
        self.insert_mode = False
        self.refresh_tool_button_states()
        self.select_block(block.id)
        self.start_inplace_edit(block.id)
        self.block_info.config(
            text=f"Trang {block.page_index + 1} | Block #{block.id} | nội dung mới — gõ trực tiếp trên box"
        )
        self.update_status_bar("Đã chèn block mới — gõ trên box, Ctrl+Enter để áp dụng")

    def get_insert_width(self) -> float:
        try:
            return max(40.0, min(600.0, float(self.insert_width_var.get())))
        except (tk.TclError, ValueError, AttributeError):
            return 240.0

    def next_block_id(self) -> int:
        return max((block.id for block in self.blocks), default=0) + 1

    def select_block(self, block_id: int):
        block = self.get_block(block_id)
        if block is None:
            return
        if self.inplace_block_id is not None and self.inplace_block_id != block_id:
            self.commit_inplace_edit()
        self.selected_block_id = block_id
        self.current_page_index = block.page_index
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", "" if block.deleted else block.text)
        family = block.font_family if block.font_family in self.font_files else (
            block.original_font_family if block.original_font_family in self.font_files else next(iter(self.font_files), "Arial")
        )
        self.font_var.set(family)
        self.font_size_var.set(round(block.font_size, 1))
        self.color_var.set(block.text_color or block.original_text_color or "#000000")
        self.bold_var.set(bool(block.bold))
        self.italic_var.set(bool(block.italic))
        self.update_editor_style()
        self.apply_block_style_spans_to_editor(block)
        self.update_block_metrics(block)
        status = "nội dung mới" if block.inserted else ("đã xóa" if block.deleted else "đã chọn")
        self.block_info.config(
            text=f"Trang {block.page_index + 1} | Block #{block.id} | {status} | double-click hoặc F2 để sửa tại chỗ"
        )
        self.draw_text_block_overlays()
        self.draw_find_highlights()
        self.update_page_thumbnail_selection()
        self.update_status_bar()

    def update_block_metrics(self, block: TextBlock):
        if not hasattr(self, "metrics_label"):
            return
        rect = block.original_rect if block.original_rect is not None else block.rect
        dirty = "có thay đổi" if block_has_changes(block) else "giống bản gốc"
        self.metrics_label.config(
            text=(
                f"Vùng gốc: x={rect.x0:.1f}  y={rect.y0:.1f}  "
                f"w={rect.width:.1f}  h={rect.height:.1f} pt\n"
                f"Font gốc: {block.original_font_family}  {block.original_font_size:.1f}pt\n"
                f"Màu gốc: {block.original_text_color}  |  "
                f"Đậm: {'có' if block.original_bold else 'không'}  "
                f"Nghiêng: {'có' if block.original_italic else 'không'}\n"
                f"Hiện tại: {block.font_family} {block.font_size:.1f}pt  {block.text_color}  ({dirty})"
            )
        )

    def apply_block_style_spans_to_editor(self, block: TextBlock):
        self.remove_style_tags("1.0", tk.END)
        text_length = len(block.text)
        if block.bold or block.italic:
            self.add_style_tag(0, text_length, (block.bold, block.italic))
        for span in block.style_spans or []:
            start = max(0, min(span.start, text_length))
            end = max(0, min(span.end, text_length))
            self.add_style_tag(start, end, (span.bold, span.italic))

    def collect_editor_style_spans(self, text_length: int, block: TextBlock | None = None) -> list[TextStyleSpan]:
        spans: list[TextStyleSpan] = []
        run_start = 0
        run_style: tuple[bool, bool] | None = None
        for offset in range(text_length):
            style = self.editor_style_at_offset(offset)
            if run_style is not None and style != run_style:
                spans.append(self._make_style_span(run_start, offset, run_style, block))
                run_start = offset
            run_style = style
        if run_style is not None:
            spans.append(self._make_style_span(run_start, text_length, run_style, block))
        return [span for span in spans if span.end > span.start]

    def _make_style_span(
        self, start: int, end: int, style: tuple[bool, bool], block: TextBlock | None
    ) -> TextStyleSpan:
        return TextStyleSpan(
            start,
            end,
            style[0],
            style[1],
            block.font_size if block else None,
            block.font_family if block else None,
            block.text_color if block else None,
        )

    def style_spans_for_plain_text(self, block: TextBlock, text: str) -> list[TextStyleSpan]:
        if not text:
            return []
        return [
            TextStyleSpan(
                0,
                len(text),
                block.bold,
                block.italic,
                block.font_size,
                block.font_family,
                block.text_color,
            )
        ]

    def get_block(self, block_id: int) -> TextBlock | None:
        return next((block for block in self.blocks if block.id == block_id), None)

    def start_inplace_edit_selected(self):
        if self.selected_block_id is None:
            messagebox.showinfo("Chưa chọn đoạn", "Hãy click vào đoạn text trên PDF trước.")
            return
        self.start_inplace_edit(self.selected_block_id)

    def start_inplace_edit(self, block_id: int, initial_text: str | None = None):
        block = self.get_block(block_id)
        if block is None or block.deleted:
            return
        if self.inplace_block_id == block_id and self.inplace_text is not None:
            self.inplace_text.focus_set()
            return
        if self.inplace_text is not None:
            self.commit_inplace_edit()

        self.selected_block_id = block_id
        rect = self.pdf_rect_to_canvas(self.expanded_text_rect(block))
        width = max(40, int(rect[2] - rect[0]))
        height = max(24, int(rect[3] - rect[1]))
        font_px = max(8, min(72, int(round(block.font_size * self.zoom * 0.92))))
        style = self.tk_font_style(block.bold, block.italic)
        text = tk.Text(
            self.canvas,
            wrap=tk.WORD,
            undo=True,
            font=(block.font_family if block.font_family in self.font_files else "Arial", font_px, style),
            fg=block.text_color if block.text_color.startswith("#") else "#000000",
            bg="#ffffff",
            insertbackground="#000000",
            relief=tk.SOLID,
            bd=1,
            highlightthickness=2,
            highlightbackground=self.colors["accent"],
            highlightcolor=self.colors["accent"],
            padx=2,
            pady=1,
        )
        content = initial_text if initial_text is not None else block.text
        text.insert("1.0", content)
        text.mark_set(tk.INSERT, "1.0")
        text.focus_set()

        window_id = self.canvas.create_window(
            rect[0],
            rect[1],
            window=text,
            anchor=tk.NW,
            width=width,
            height=height,
            tags=("inplace_editor",),
        )
        self.inplace_text = text
        self.inplace_window_id = window_id
        self.inplace_block_id = block_id

        text.bind("<Control-Return>", lambda _e: self._shortcut(self.apply_text_change))
        text.bind("<Escape>", lambda _e: self.cancel_inplace_edit() or "break")
        text.bind("<FocusOut>", self._on_inplace_focus_out)
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", content)
        self.apply_block_style_spans_to_editor(block)
        self.update_status_bar("Đang sửa tại chỗ — Ctrl+Enter áp dụng, Esc hủy")

    def _on_inplace_focus_out(self, _event=None):
        if self._inplace_committing or self.inplace_text is None:
            return
        # Defer so clicks on Apply / toolbar still see the editor content.
        self.after(120, self._maybe_commit_inplace_on_focus_out)

    def _maybe_commit_inplace_on_focus_out(self):
        if self.inplace_text is None or self._inplace_committing:
            return
        focused = self.focus_get()
        if focused is self.inplace_text:
            return
        # Keep editing if focus moved to style controls / panel editor intentionally
        if focused in {self.editor, getattr(self, "font_combo", None), getattr(self, "font_size_spin", None)}:
            return
        self.commit_inplace_edit()

    def cancel_inplace_edit(self):
        self.destroy_inplace_editor(commit=False)
        if self.selected_block_id is not None:
            self.select_block(self.selected_block_id)
        self.update_status_bar("Đã hủy sửa tại chỗ")

    def commit_inplace_edit(self, apply_styles_from_panel: bool = False):
        if self.inplace_text is None or self.inplace_block_id is None:
            return
        block = self.get_block(self.inplace_block_id)
        if block is None:
            self.destroy_inplace_editor(commit=False)
            return
        new_text = self.inplace_text.get("1.0", "end-1c")
        self.destroy_inplace_editor(commit=False)
        self._apply_text_to_block(block, new_text, apply_styles_from_panel=apply_styles_from_panel, from_inplace=True)
        self.render_current_page()
        self.select_block(block.id)
        self.update_status_bar("Đã áp dụng sửa tại chỗ — chưa lưu file")

    def destroy_inplace_editor(self, commit: bool = False):
        if commit and self.inplace_text is not None:
            self.commit_inplace_edit()
            return
        self._inplace_committing = True
        try:
            if self.inplace_window_id is not None:
                try:
                    self.canvas.delete(self.inplace_window_id)
                except tk.TclError:
                    pass
            if self.inplace_text is not None:
                try:
                    self.inplace_text.destroy()
                except tk.TclError:
                    pass
        finally:
            self.inplace_text = None
            self.inplace_window_id = None
            self.inplace_block_id = None
            self._inplace_committing = False

    def _apply_text_to_block(
        self,
        block: TextBlock,
        new_text: str,
        apply_styles_from_panel: bool = False,
        from_inplace: bool = False,
    ):
        block.text = new_text
        block.deleted = False
        if apply_styles_from_panel:
            block.font_family = self.font_var.get() or block.font_family
            block.font_size = self.get_selected_font_size(default=block.font_size)
            block.text_color = self.color_var.get() or block.text_color
            block.bold = bool(self.bold_var.get())
            block.italic = bool(self.italic_var.get())
            if from_inplace:
                block.style_spans = self.style_spans_for_plain_text(block, new_text)
            else:
                collected = self.collect_editor_style_spans(len(new_text), block)
                block.style_spans = collected or self.style_spans_for_plain_text(block, new_text)
        else:
            # Keep original formatting; only rebuild span ranges for new text length.
            if new_text == block.original_text and not block.inserted:
                block.font_family = block.original_font_family
                block.font_size = block.original_font_size
                block.text_color = block.original_text_color
                block.bold = block.original_bold
                block.italic = block.original_italic
                block.style_spans = [
                    TextStyleSpan(
                        s.start,
                        s.end,
                        s.bold,
                        s.italic,
                        s.font_size,
                        s.font_family,
                        s.text_color,
                    )
                    for s in (block.original_style_spans or [])
                ]
            else:
                block.style_spans = self.style_spans_for_plain_text(block, new_text)

    def apply_text_change(self):
        if self.selected_block_id is None and self.inplace_block_id is None:
            messagebox.showinfo(
                "Chưa chọn đoạn",
                "Hãy click vào đoạn text trên PDF trước, hoặc bấm Thêm nội dung để chèn text mới.",
            )
            return
        if self.inplace_text is not None:
            # In-place: text from box; styles from panel only if user changed them.
            block = self.get_block(self.inplace_block_id)
            if block is None:
                return
            new_text = self.inplace_text.get("1.0", "end-1c")
            styles_changed = self._panel_styles_differ_from_block(block)
            self.destroy_inplace_editor(commit=False)
            self._apply_text_to_block(block, new_text, apply_styles_from_panel=styles_changed, from_inplace=True)
            if styles_changed:
                # Panel intentionally overrides formatting
                block.font_family = self.font_var.get() or block.font_family
                block.font_size = self.get_selected_font_size(default=block.font_size)
                block.text_color = self.color_var.get() or block.text_color
                block.bold = bool(self.bold_var.get())
                block.italic = bool(self.italic_var.get())
                block.style_spans = self.style_spans_for_plain_text(block, new_text)
            self.render_current_page()
            self.refresh_thumbnail_for_page(block.page_index)
            self.select_block(block.id)
            self.block_info.config(
                text=f"Trang {block.page_index + 1} | Block #{block.id} | đã áp dụng (giữ format), chưa lưu file"
            )
            self.update_status_bar("Đã áp dụng sửa — chưa lưu file")
            return

        block = self.get_block(self.selected_block_id)
        if block is None:
            return
        new_text = self.editor.get("1.0", tk.END).rstrip()
        styles_changed = self._panel_styles_differ_from_block(block)
        self._apply_text_to_block(block, new_text, apply_styles_from_panel=styles_changed, from_inplace=False)
        if styles_changed:
            block.font_family = self.font_var.get() or block.font_family
            block.font_size = self.get_selected_font_size(default=block.font_size)
            block.text_color = self.color_var.get() or block.text_color
            block.bold = bool(self.bold_var.get())
            block.italic = bool(self.italic_var.get())
            collected = self.collect_editor_style_spans(len(new_text), block)
            block.style_spans = collected or self.style_spans_for_plain_text(block, new_text)
        self.select_block(block.id)
        self.block_info.config(
            text=f"Trang {block.page_index + 1} | Block #{block.id} | đã áp dụng (giữ format), chưa lưu file"
        )
        self.render_current_page()
        self.refresh_thumbnail_for_page(block.page_index)
        self.update_status_bar("Đã áp dụng sửa — chưa lưu file")

    def _panel_styles_differ_from_block(self, block: TextBlock) -> bool:
        family = self.font_var.get() or block.font_family
        size = self.get_selected_font_size(default=block.font_size)
        color = (self.color_var.get() or block.text_color or "").lower()
        return (
            family != block.font_family
            or abs(size - block.font_size) >= 0.05
            or color != (block.text_color or "").lower()
            or bool(self.bold_var.get()) != bool(block.bold)
            or bool(self.italic_var.get()) != bool(block.italic)
        )

    def delete_selected_block(self):
        if self.inplace_text is not None:
            self.destroy_inplace_editor(commit=False)
        if self.selected_block_id is None:
            messagebox.showinfo("Chưa chọn đoạn", "Hãy click vào đoạn text cần xóa trên PDF trước.")
            return
        block = self.get_block(self.selected_block_id)
        if block is None:
            return
        if block.inserted:
            removed_id = block.id
            self.blocks = [item for item in self.blocks if item.id != removed_id]
            self.selected_block_id = None
            self.editor.delete("1.0", tk.END)
            self.block_info.config(text=f"Đã xóa nội dung mới #{removed_id}, chưa lưu file")
            self.render_current_page()
            return
        block.text = ""
        block.deleted = True
        self.editor.delete("1.0", tk.END)
        self.select_block(block.id)
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | đã đánh dấu xóa, chưa lưu file")
        self.render_current_page()

    def restore_selected_block(self):
        if self.selected_block_id is None:
            messagebox.showinfo("Chưa chọn đoạn", "Hãy click vào đoạn text cần khôi phục trước.")
            return
        block = self.get_block(self.selected_block_id)
        if block is None:
            return
        if block.inserted:
            removed_id = block.id
            self.blocks = [item for item in self.blocks if item.id != removed_id]
            self.selected_block_id = None
            self.editor.delete("1.0", tk.END)
            self.block_info.config(text=f"Đã hủy nội dung mới #{removed_id}")
            self.render_current_page()
            return
        self.destroy_inplace_editor(commit=False)
        block.text = block.original_text
        if block.original_rect is not None:
            block.rect = copy.copy(block.original_rect)
        block.font_size = block.original_font_size
        block.font_family = block.original_font_family
        block.text_color = block.original_text_color
        block.bold = block.original_bold
        block.italic = block.original_italic
        block.style_spans = [
            TextStyleSpan(s.start, s.end, s.bold, s.italic, s.font_size, s.font_family, s.text_color)
            for s in (block.original_style_spans or [])
        ]
        block.deleted = False
        block.manually_resized = False
        self.select_block(block.id)
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | đã khôi phục format gốc")
        self.render_current_page()

    def previous_page(self):
        if self.doc is None:
            return
        if self.current_page_index > 0:
            self.goto_page(self.current_page_index - 1)

    def next_page(self):
        if self.doc is None:
            return
        if self.current_page_index < len(self.doc) - 1:
            self.goto_page(self.current_page_index + 1)

    def goto_page(self, page_index: int):
        if self.doc is None or len(self.doc) == 0:
            return
        page_index = max(0, min(int(page_index), len(self.doc) - 1))
        if page_index == self.current_page_index and self.selected_block_id is None:
            self.update_page_thumbnail_selection()
            return
        self.commit_inplace_edit()
        self.current_page_index = page_index
        self.selected_block_id = None
        self.insert_mode = False
        self.resize_state = None
        self.move_state = None
        self.editor.delete("1.0", tk.END)
        self.block_info.config(text="Chưa chọn đoạn")
        if hasattr(self, "metrics_label"):
            self.metrics_label.config(text="Chọn một đoạn trên PDF để xem thông số gốc.")
        self.refresh_tool_button_states()
        self.render_current_page()

    def change_zoom(self, delta: float):
        self.set_zoom(self.zoom + delta)

    def set_zoom(self, zoom: float):
        self.zoom = max(0.5, min(3.0, zoom))
        self.render_current_page()

    def _on_viewer_enter_for_scroll(self, _event=None):
        """Allow mouse-wheel scroll over the preview without breaking text editing focus."""
        focused = self.focus_get()
        if focused is self.inplace_text or focused is self.editor:
            return
        if focused is getattr(self, "find_entry", None):
            return
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass

    def on_viewer_mousewheel(self, event):
        """Scroll the PDF preview with the mouse wheel (Ctrl+wheel still zooms)."""
        if self.doc is None:
            return
        # If user is typing in an editor that currently has focus, don't steal the wheel.
        focused = self.focus_get()
        if focused in {self.inplace_text, self.editor, getattr(self, "find_entry", None)}:
            return None
        state = int(getattr(event, "state", 0) or 0)
        if state & 0x0004:  # Control → zoom (also bound separately)
            return None
        if state & 0x0001:  # Shift → horizontal
            return self.on_viewer_shift_mousewheel(event)
        delta = self._wheel_units(event)
        if delta == 0:
            return "break"
        self.canvas.yview_scroll(delta, "units")
        return "break"

    def on_viewer_shift_mousewheel(self, event):
        if self.doc is None:
            return
        delta = self._wheel_units(event)
        if delta == 0:
            return "break"
        self.canvas.xview_scroll(delta, "units")
        return "break"

    def on_viewer_button4(self, event):
        state = int(getattr(event, "state", 0) or 0)
        if state & 0x0004:
            return self.zoom_at_event(event, 0.15)
        if state & 0x0001:
            return self._viewer_scroll_x(-1)
        return self._viewer_scroll_y(-1)

    def on_viewer_button5(self, event):
        state = int(getattr(event, "state", 0) or 0)
        if state & 0x0004:
            return self.zoom_at_event(event, -0.15)
        if state & 0x0001:
            return self._viewer_scroll_x(1)
        return self._viewer_scroll_y(1)

    def _viewer_scroll_y(self, units: int):
        if self.doc is None:
            return "break"
        self.canvas.yview_scroll(units, "units")
        return "break"

    def _viewer_scroll_x(self, units: int):
        if self.doc is None:
            return "break"
        self.canvas.xview_scroll(units, "units")
        return "break"

    def _wheel_units(self, event) -> int:
        delta = getattr(event, "delta", 0) or 0
        if delta == 0:
            return 0
        # Windows: multiples of 120; some devices send smaller values.
        steps = int(-delta / 120)
        if steps == 0:
            steps = -1 if delta > 0 else 1
        return steps

    def on_ctrl_mousewheel_zoom(self, event):
        delta = 0.15 if event.delta > 0 else -0.15
        self.zoom_at_event(event, delta)
        return "break"

    def zoom_at_event(self, event, delta: float):
        if self.doc is None:
            return "break"
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        old_zoom = self.zoom
        pdf_x = (canvas_x - self.page_origin_x) / old_zoom
        pdf_y = (canvas_y - self.page_origin_y) / old_zoom
        self.set_zoom(self.zoom + delta)
        if old_zoom <= 0:
            return "break"

        new_x = self.page_origin_x + pdf_x * self.zoom
        new_y = self.page_origin_y + pdf_y * self.zoom
        scrollregion = self.canvas.bbox("all")
        if not scrollregion:
            return "break"
        total_width = max(1, scrollregion[2] - scrollregion[0])
        total_height = max(1, scrollregion[3] - scrollregion[1])
        self.canvas.xview_moveto(max(0.0, min(1.0, (new_x - event.x) / total_width)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (new_y - event.y) / total_height)))
        return "break"

    def save_pdf(self):
        if self.doc is None or self.pdf_path is None:
            messagebox.showinfo("Chưa có PDF", "Hãy mở một file PDF trước.")
            return False
        output_path = filedialog.asksaveasfilename(
            title="Lưu PDF mới",
            defaultextension=".pdf",
            initialfile=self._default_output_name(),
            filetypes=[("PDF files", "*.pdf")],
        )
        if not output_path:
            return False
        try:
            out_doc = fitz.open(self.pdf_path)
            self.apply_blocks_to_pdf(out_doc)
            out_doc.save(output_path, garbage=4, deflate=True)
            out_doc.close()
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không thể lưu PDF:\n{exc}")
            return False
        self.structure_dirty = False
        messagebox.showinfo("Hoàn tất", f"Đã lưu PDF mới:\n{output_path}")
        self.update_status_bar(f"Đã lưu {os.path.basename(output_path)}")
        return True

    def _default_output_name(self):
        source = self.original_pdf_path or self.pdf_path
        if not source:
            return "edited.pdf"
        base, ext = os.path.splitext(os.path.basename(source))
        if base.endswith("_edited"):
            return f"{base}{ext or '.pdf'}"
        return f"{base}_edited{ext or '.pdf'}"

    def apply_blocks_to_pdf(self, doc: fitz.Document):
        apply_blocks_to_pdf(doc, self.blocks, self.doc, self.font_files, self.pdf_font_path)

    def padded_rect(self, rect: fitz.Rect, page_index: int, padding: float) -> fitz.Rect:
        return padded_rect(rect, self.doc, page_index, padding)

    def wrap_text_for_pdf(self, text: str, font_size: float, max_width: float) -> list[str]:
        return wrap_text_for_pdf(text, font_size, max_width)

    def _fit_font_size(self, rect: fitz.Rect, text: str, preferred_size: float) -> float:
        return fit_font_size(rect, text, preferred_size)


if __name__ == "__main__":
    app = PdfParagraphEditor()
    app.mainloop()
