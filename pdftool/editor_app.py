import copy
import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import fitz

from .font_manager import available_font_files, find_unicode_font
from .geometry import (
    constrain_moved_rect_to_page,
    constrain_rect_to_page,
    resize_handle_points,
)
from .models import TextBlock, TextStyleSpan
from .pdf_engine import (
    apply_blocks_to_pdf,
    expanded_text_rect,
    extract_text_blocks,
    padded_rect,
    styled_wrapped_lines,
)
from .text_layout import fit_font_size, required_text_height, wrap_text_for_canvas, wrap_text_for_pdf
from .update_checker import is_newer_version, latest_github_tag, open_latest_release
from .version import APP_VERSION


class PdfParagraphEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PDF Visual Text Editor v{APP_VERSION}")
        self.geometry("1280x820")
        self.minsize(1080, 700)

        self.pdf_path: str | None = None
        self.doc: fitz.Document | None = None
        self.blocks: list[TextBlock] = []
        self.selected_block_id: int | None = None
        self.current_page_index = 0
        self.zoom = 1.35
        self.page_photo: tk.PhotoImage | None = None
        self.page_canvas_item: int | None = None
        self.page_origin_x = 20
        self.page_origin_y = 20
        self.insert_mode = False
        self.resize_state: dict | None = None
        self.move_state: dict | None = None
        self.font_files = available_font_files()
        self.pdf_font_path = self.font_files.get("Arial") or find_unicode_font(self.font_files)

        self._build_ui()

    def update_editor_style(self, _event=None):
        font_family = self.font_var.get() or "Arial"
        color = self.color_var.get() or "#000000"
        font_size = self.get_selected_font_size(default=11)
        self.editor.configure(font=(font_family, max(6, min(72, int(font_size))), "normal"), foreground=color)
        self.configure_editor_style_tags()
        self.color_preview.configure(bg=color)

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
                f"Phiên bản hiện tại: v{APP_VERSION}\nPhiên bản mới nhất: {latest_tag}\n\nMở trang tải bản mới?",
            )
            if should_open:
                open_latest_release(latest_tag)
            return
        messagebox.showinfo("Đã là bản mới nhất", f"Phiên bản hiện tại: v{APP_VERSION}\nTag mới nhất trên GitHub: {latest_tag}")

    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Mở PDF", command=self.open_pdf).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Lưu thành PDF mới", command=self.save_pdf).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Kiểm tra cập nhật", command=self.check_for_updates).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(toolbar, text="Trang trước", command=self.previous_page).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Trang sau", command=self.next_page).pack(side=tk.LEFT, padx=(6, 0))
        self.page_label = ttk.Label(toolbar, text="Trang -/-")
        self.page_label.pack(side=tk.LEFT, padx=10)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(toolbar, text="Thu nhỏ", command=lambda: self.change_zoom(-0.15)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Phóng to", command=lambda: self.change_zoom(0.15)).pack(side=tk.LEFT, padx=(6, 0))
        self.zoom_label = ttk.Label(toolbar, text="Zoom 135%")
        self.zoom_label.pack(side=tk.LEFT, padx=10)

        self.file_label = ttk.Label(toolbar, text="Chưa mở file PDF")
        self.file_label.pack(side=tk.RIGHT)

        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        viewer_frame = ttk.Frame(main)
        editor_frame = ttk.Frame(main, width=360)
        main.add(viewer_frame, weight=4)
        main.add(editor_frame, weight=1)

        hint = "Click vào text để chọn. Kéo trong box để di chuyển, kéo các ô vuông quanh box để đổi kích thước."
        ttk.Label(viewer_frame, text=hint, foreground="#444").pack(anchor=tk.W, pady=(0, 6))

        canvas_shell = ttk.Frame(viewer_frame)
        canvas_shell.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_shell, bg="#777", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(canvas_shell, orient=tk.VERTICAL, command=self.canvas.yview)
        x_scroll = ttk.Scrollbar(canvas_shell, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        canvas_shell.rowconfigure(0, weight=1)
        canvas_shell.columnconfigure(0, weight=1)
        self.canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.canvas.bind("<Button-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel_zoom)
        self.canvas.bind("<Control-Button-4>", lambda event: self.zoom_at_event(event, 0.15))
        self.canvas.bind("<Control-Button-5>", lambda event: self.zoom_at_event(event, -0.15))
        self.bind_all("<Control-plus>", lambda event: self.change_zoom(0.15))
        self.bind_all("<Control-equal>", lambda event: self.change_zoom(0.15))
        self.bind_all("<Control-minus>", lambda event: self.change_zoom(-0.15))
        self.bind_all("<Control-0>", lambda event: self.set_zoom(1.0))

        ttk.Label(editor_frame, text="Nội dung đoạn đã chọn").pack(anchor=tk.W)
        self.block_info = ttk.Label(editor_frame, text="Chưa chọn đoạn", foreground="#555", wraplength=330)
        self.block_info.pack(anchor=tk.W, pady=(4, 8))

        self.editor = tk.Text(editor_frame, wrap=tk.WORD, undo=True, font=("Segoe UI", 11), height=18)
        self.editor.pack(fill=tk.BOTH, expand=True)

        style_frame = ttk.LabelFrame(editor_frame, text="Font và màu chữ", padding=8)
        style_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(style_frame, text="Font:").grid(row=0, column=0, sticky=tk.W)
        self.font_var = tk.StringVar(value="Arial")
        self.font_combo = ttk.Combobox(
            style_frame,
            textvariable=self.font_var,
            values=list(self.font_files.keys()) or ["Arial"],
            state="readonly",
            width=20,
        )
        self.font_combo.grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))
        self.font_combo.bind("<<ComboboxSelected>>", self.update_editor_style)

        style_options = ttk.Frame(style_frame)
        style_options.grid(row=1, column=1, sticky=tk.W, padx=(6, 0), pady=(6, 0))
        self.bold_var = tk.BooleanVar(value=False)
        self.italic_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(style_options, text="In đậm", variable=self.bold_var, command=self.toggle_bold_selection).pack(side=tk.LEFT)
        ttk.Checkbutton(style_options, text="In nghiêng", variable=self.italic_var, command=self.toggle_italic_selection).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(style_frame, text="Kiểu chữ:").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))

        ttk.Label(style_frame, text="Cỡ chữ:").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.font_size_var = tk.DoubleVar(value=11.0)
        size_frame = ttk.Frame(style_frame)
        size_frame.grid(row=2, column=1, sticky=tk.EW, padx=(6, 0), pady=(6, 0))
        self.font_size_spin = ttk.Spinbox(
            size_frame,
            from_=6,
            to=72,
            increment=1,
            textvariable=self.font_size_var,
            width=8,
            command=self.update_editor_style,
        )
        self.font_size_spin.pack(side=tk.LEFT)
        self.font_size_spin.bind("<KeyRelease>", self.update_editor_style)
        self.font_size_spin.bind("<FocusOut>", self.update_editor_style)
        ttk.Label(size_frame, text="pt").pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(style_frame, text="Màu:").grid(row=3, column=0, sticky=tk.W, pady=(6, 0))
        self.color_var = tk.StringVar(value="#000000")
        self.color_preview = tk.Label(style_frame, text="      ", bg="#000000", relief=tk.SUNKEN)
        self.color_preview.grid(row=3, column=1, sticky=tk.W, padx=(6, 0), pady=(6, 0))
        ttk.Button(style_frame, text="Chọn màu", command=self.choose_text_color).grid(row=3, column=1, sticky=tk.E, pady=(6, 0))
        style_frame.columnconfigure(1, weight=1)

        actions = ttk.Frame(editor_frame)
        actions.pack(fill=tk.X, pady=8)
        ttk.Button(actions, text="Áp dụng sửa", command=self.apply_text_change).pack(side=tk.LEFT)
        ttk.Button(actions, text="Xóa đoạn", command=self.delete_selected_block).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(actions, text="Khôi phục", command=self.restore_selected_block).pack(side=tk.LEFT, padx=(6, 0))

        insert_frame = ttk.LabelFrame(editor_frame, text="Thêm nội dung mới", padding=8)
        insert_frame.pack(fill=tk.X, pady=(0, 8))
        self.insert_button = ttk.Button(insert_frame, text="Thêm nội dung", command=self.enable_insert_mode)
        self.insert_button.pack(side=tk.LEFT)
        ttk.Label(insert_frame, text="Bề rộng:").pack(side=tk.LEFT, padx=(10, 4))
        self.insert_width_var = tk.DoubleVar(value=240.0)
        self.insert_width_spin = ttk.Spinbox(insert_frame, from_=40, to=600, increment=10, textvariable=self.insert_width_var, width=7)
        self.insert_width_spin.pack(side=tk.LEFT)
        ttk.Label(insert_frame, text="pt").pack(side=tk.LEFT, padx=(4, 0))

        note = (
            "Giới hạn: PDF scan ảnh cần OCR. Khi lưu, chương trình che vùng text cũ và ghi text mới lên trên, "
            "không chỉnh sửa stream text gốc như Word."
        )
        ttk.Label(editor_frame, text=note, foreground="#666", wraplength=330).pack(anchor=tk.W, pady=(8, 0))

    def open_pdf(self):
        path = filedialog.askopenfilename(title="Chọn file PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path:
            return

        try:
            doc = fitz.open(path)
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không thể mở PDF:\n{exc}")
            return

        self.pdf_path = path
        self.doc = doc
        self.blocks = self.extract_text_blocks(doc)
        self.selected_block_id = None
        self.insert_mode = False
        self.resize_state = None
        self.move_state = None
        self.current_page_index = 0
        self.file_label.config(text=os.path.basename(path))
        self.editor.delete("1.0", tk.END)
        self.render_current_page()

        if not self.blocks:
            messagebox.showwarning("Không tìm thấy text", "Không trích xuất được text. PDF này có thể là file scan ảnh.")

    def extract_text_blocks(self, doc: fitz.Document) -> list[TextBlock]:
        return extract_text_blocks(doc)

    def render_current_page(self):
        if self.doc is None or len(self.doc) == 0:
            return
        self.current_page_index = max(0, min(self.current_page_index, len(self.doc) - 1))
        page = self.doc[self.current_page_index]
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        self.page_photo = tk.PhotoImage(data=pix.tobytes("ppm"))

        self.canvas.delete("all")
        self.page_canvas_item = self.canvas.create_image(self.page_origin_x, self.page_origin_y, image=self.page_photo, anchor=tk.NW)
        width = pix.width + self.page_origin_x * 2
        height = pix.height + self.page_origin_y * 2
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.page_label.config(text=f"Trang {self.current_page_index + 1}/{len(self.doc)}")
        self.zoom_label.config(text=f"Zoom {int(self.zoom * 100)}%")
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()

    def draw_edited_block_preview(self):
        self.canvas.delete("edit_preview")
        for block in self.blocks_on_current_page():
            if (
                not block.inserted
                and
                not block.deleted
                and block.text == block.original_text
                and abs(block.font_size - block.original_font_size) < 0.01
                and block.font_family == "Arial"
                and block.text_color == "#000000"
                and not block.bold
                and not block.italic
                and not block.style_spans
            ):
                continue

            expanded_pdf_rect = self.expanded_text_rect(block)
            rect = self.pdf_rect_to_canvas(expanded_pdf_rect)
            if not block.inserted:
                if block.manually_resized and block.original_rect is not None:
                    original_rect = self.pdf_rect_to_canvas(self.padded_rect(block.original_rect, block.page_index, 1))
                    self.canvas.create_rectangle(*original_rect, outline="", fill="white", tags="edit_preview")
                self.canvas.create_rectangle(*rect, outline="", fill="white", tags="edit_preview")

            if block.deleted or not block.text.strip():
                continue

            font_size = max(6, int(block.font_size * self.zoom))
            x = rect[0] + 2
            y = rect[1] + 2
            max_width = max(20, rect[2] - rect[0] - 4)
            wrapped_lines = styled_wrapped_lines(block.text, font_size, max_width, block.bold, block.italic, block.style_spans or [])
            for line in wrapped_lines:
                if y > rect[3] - font_size:
                    break
                segment_x = x
                for segment in line:
                    segment_text = segment["text"]
                    if not segment_text:
                        continue
                    self.canvas.create_text(
                        segment_x,
                        y,
                        text=segment_text,
                        anchor=tk.NW,
                        fill=block.text_color,
                        font=(block.font_family, font_size, self.tk_font_style(segment["bold"], segment["italic"])),
                        tags="edit_preview",
                    )
                    segment_x += len(segment_text) * font_size * 0.55
                y += int(font_size * 1.25)

    def expanded_text_rect(self, block: TextBlock) -> fitz.Rect:
        return expanded_text_rect(block, self.doc)

    def required_text_height(self, text: str, font_size: float, max_width: float) -> float:
        return required_text_height(text, font_size, max_width)

    def wrap_text_for_canvas(self, text: str, font_size: int, max_width: float) -> list[str]:
        return wrap_text_for_canvas(text, font_size, max_width)

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
        if self.insert_mode:
            self.create_inserted_block(point)
            return

        handle = self.find_resize_handle(canvas_x, canvas_y)
        if handle:
            block = self.get_block(self.selected_block_id) if self.selected_block_id is not None else None
            if block is not None:
                block.rect = self.expanded_text_rect(block)
                self.resize_state = {
                    "block_id": block.id,
                    "handle": handle,
                    "start_rect": copy.copy(block.rect),
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
            self.selected_block_id = None
            self.editor.delete("1.0", tk.END)
            self.block_info.config(text="Chưa chọn đoạn")
            self.draw_text_block_overlays()
            return
        self.select_block(clicked.id)
        clicked.rect = self.expanded_text_rect(clicked)
        self.move_state = {
            "block_id": clicked.id,
            "start_rect": copy.copy(clicked.rect),
            "start_x": canvas_x,
            "start_y": canvas_y,
            "moved": False,
        }
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
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.block_info.config(
            text=f"Trang {block.page_index + 1} | Block #{block.id} | đang kéo kích thước {block.rect.width:.0f} x {block.rect.height:.0f} pt"
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
                self.draw_edited_block_preview()
                self.block_info.config(
                    text=f"Trang {block.page_index + 1} | Block #{block.id} | đã đổi kích thước {block.rect.width:.0f} x {block.rect.height:.0f} pt, chưa lưu file"
                )
            return "break"
        if self.move_state:
            block = self.get_block(self.move_state["block_id"])
            moved = self.move_state.get("moved", False)
            self.move_state = None
            if block is not None and moved:
                self.select_block(block.id)
                self.draw_edited_block_preview()
                self.block_info.config(
                    text=f"Trang {block.page_index + 1} | Block #{block.id} | đã di chuyển tới x={block.rect.x0:.0f}, y={block.rect.y0:.0f} pt, chưa lưu file"
                )
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
        self.draw_text_block_overlays()

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
        block = TextBlock(
            id=block_id,
            page_index=self.current_page_index,
            rect=fitz.Rect(x0, y0, x1, y1),
            text="",
            original_text="",
            font_size=font_size,
            original_font_size=font_size,
            font_family=self.font_var.get() or "Arial",
            text_color=self.color_var.get() or "#000000",
            inserted=True,
        )
        self.blocks.append(block)
        self.insert_mode = False
        self.select_block(block.id)
        self.editor.focus_set()
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | nội dung mới, nhập text rồi bấm Áp dụng sửa")

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
        self.selected_block_id = block_id
        self.current_page_index = block.page_index
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", "" if block.deleted else block.text)
        self.font_var.set(block.font_family if block.font_family in self.font_files else "Arial")
        self.font_size_var.set(round(block.font_size, 1))
        self.color_var.set(block.text_color)
        self.bold_var.set(False)
        self.italic_var.set(False)
        self.update_editor_style()
        self.apply_block_style_spans_to_editor(block)
        status = "nội dung mới" if block.inserted else ("đã xóa" if block.deleted else "đang sửa")
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | {status} | font ~{block.font_size:.1f}")
        self.draw_text_block_overlays()

    def apply_block_style_spans_to_editor(self, block: TextBlock):
        self.remove_style_tags("1.0", tk.END)
        text_length = len(block.text)
        if block.bold or block.italic:
            self.add_style_tag(0, text_length, (block.bold, block.italic))
        for span in block.style_spans or []:
            start = max(0, min(span.start, text_length))
            end = max(0, min(span.end, text_length))
            self.add_style_tag(start, end, (span.bold, span.italic))

    def collect_editor_style_spans(self, text_length: int) -> list[TextStyleSpan]:
        spans: list[TextStyleSpan] = []
        run_start = 0
        run_style: tuple[bool, bool] | None = None
        for offset in range(text_length):
            style = self.editor_style_at_offset(offset)
            if run_style is not None and style != run_style:
                if run_style != (False, False):
                    spans.append(TextStyleSpan(run_start, offset, run_style[0], run_style[1]))
                run_start = offset
            run_style = style
        if run_style is not None and run_style != (False, False):
            spans.append(TextStyleSpan(run_start, text_length, run_style[0], run_style[1]))
        return spans

    def get_block(self, block_id: int) -> TextBlock | None:
        return next((block for block in self.blocks if block.id == block_id), None)

    def apply_text_change(self):
        if self.selected_block_id is None:
            messagebox.showinfo("Chưa chọn đoạn", "Hãy click vào đoạn text trên PDF trước, hoặc bấm Thêm nội dung để chèn text mới.")
            return
        block = self.get_block(self.selected_block_id)
        if block is None:
            return
        block.text = self.editor.get("1.0", tk.END).rstrip()
        block.font_family = self.font_var.get() or "Arial"
        block.font_size = self.get_selected_font_size(default=block.font_size)
        block.text_color = self.color_var.get() or "#000000"
        block.bold = False
        block.italic = False
        block.style_spans = self.collect_editor_style_spans(len(block.text))
        block.deleted = False
        self.select_block(block.id)
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | đã áp dụng sửa, chưa lưu file")

    def delete_selected_block(self):
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
            self.draw_edited_block_preview()
            self.draw_text_block_overlays()
            return
        block.text = ""
        block.deleted = True
        self.editor.delete("1.0", tk.END)
        self.select_block(block.id)
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | đã đánh dấu xóa, chưa lưu file")

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
            self.draw_edited_block_preview()
            self.draw_text_block_overlays()
            return
        block.text = block.original_text
        if block.original_rect is not None:
            block.rect = copy.copy(block.original_rect)
        block.font_size = block.original_font_size
        block.font_family = "Arial"
        block.text_color = "#000000"
        block.bold = False
        block.italic = False
        block.style_spans = []
        block.deleted = False
        block.manually_resized = False
        self.select_block(block.id)
        self.draw_edited_block_preview()
        self.draw_text_block_overlays()
        self.block_info.config(text=f"Trang {block.page_index + 1} | Block #{block.id} | đã khôi phục, chưa lưu file")

    def previous_page(self):
        if self.doc is None:
            return
        if self.current_page_index > 0:
            self.current_page_index -= 1
            self.selected_block_id = None
            self.insert_mode = False
            self.resize_state = None
            self.move_state = None
            self.editor.delete("1.0", tk.END)
            self.block_info.config(text="Chưa chọn đoạn")
            self.render_current_page()

    def next_page(self):
        if self.doc is None:
            return
        if self.current_page_index < len(self.doc) - 1:
            self.current_page_index += 1
            self.selected_block_id = None
            self.insert_mode = False
            self.resize_state = None
            self.move_state = None
            self.editor.delete("1.0", tk.END)
            self.block_info.config(text="Chưa chọn đoạn")
            self.render_current_page()

    def change_zoom(self, delta: float):
        self.set_zoom(self.zoom + delta)

    def set_zoom(self, zoom: float):
        self.zoom = max(0.5, min(3.0, zoom))
        self.render_current_page()

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
            return
        output_path = filedialog.asksaveasfilename(
            title="Lưu PDF mới",
            defaultextension=".pdf",
            initialfile=self._default_output_name(),
            filetypes=[("PDF files", "*.pdf")],
        )
        if not output_path:
            return
        try:
            out_doc = fitz.open(self.pdf_path)
            self.apply_blocks_to_pdf(out_doc)
            out_doc.save(output_path, garbage=4, deflate=True)
            out_doc.close()
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không thể lưu PDF:\n{exc}")
            return
        messagebox.showinfo("Hoàn tất", f"Đã lưu PDF mới:\n{output_path}")

    def _default_output_name(self):
        if not self.pdf_path:
            return "edited.pdf"
        base, ext = os.path.splitext(os.path.basename(self.pdf_path))
        return f"{base}_edited{ext}"

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
