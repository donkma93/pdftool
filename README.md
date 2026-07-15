# PDF Paragraph Editor

Ứng dụng desktop Python để mở PDF như trình xem PDF bình thường, click trực tiếp vào đoạn text để sửa hoặc xóa, rồi xuất ra PDF mới.

**Developer:** donpv  
**Copyright © 2026 donpv. All rights reserved.**

## Cài đặt

```powershell
pip install -r requirements.txt
```

## Chạy chương trình

```powershell
python pdf_editor.py
```

## Phiên bản và cập nhật

Phiên bản hiện tại được khai báo trong `pdftool/version.py`.

Quy trình phát hành version mới:

```powershell
# 1. Sửa APP_VERSION trong pdftool/version.py, ví dụ 1.1.0
git add .
git commit -m "Release v1.1.0"
git tag v1.1.0
git push origin main
git push origin v1.1.0
```

Trong app có nút `Kiểm tra cập nhật`. Chương trình sẽ đọc tag mới nhất từ repo GitHub `donkma93/pdftool`; nếu tag mới hơn `APP_VERSION`, app sẽ mở trang release/tag tương ứng.

## Kiến trúc dự án

```text
PDFTOOL/
├─ pdf_editor.py              # Launcher, giữ cách chạy cũ
├─ pdftool/
│  ├─ main.py                 # Entry point của ứng dụng
│  ├─ editor_app.py           # UI Tkinter và điều phối thao tác người dùng
│  ├─ models.py               # Các model dữ liệu, ví dụ TextBlock
│  ├─ pdf_engine.py           # Trích xuất text, ghi nội dung, xuất PDF
│  ├─ text_layout.py          # Wrap text, ước lượng chiều cao, fit font
│  ├─ geometry.py             # Resize/move box, giới hạn trong trang
│  └─ font_manager.py         # Tìm font hệ thống
├─ requirements.txt
└─ PDFTextEditor.spec
```

Hướng mở rộng:

- Thêm tính năng xử lý PDF thì ưu tiên đặt trong `pdftool/pdf_engine.py`.
- Thêm logic kéo, resize, snap, align box thì đặt trong `pdftool/geometry.py`.
- Thêm model mới thì đặt trong `pdftool/models.py`.
- Chỉ đưa code giao diện và event Tkinter vào `pdftool/editor_app.py`.

## Giới hạn kỹ thuật

PDF không lưu nội dung giống file Word. Bản hiện tại:

- Render trang PDF trực tiếp trong cửa sổ phần mềm.
- Click vào đoạn text để chọn đoạn cần sửa.
- Sửa nội dung trong panel bên phải, bấm `Áp dụng`.
- Bấm `Xóa đoạn` để xóa đoạn đã chọn.
- Khi xuất, chương trình che vùng text cũ bằng nền trắng và ghi text mới lên trên.
- Giao diện Phase A: menu, tooltip toolbar, status bar, welcome screen, find bar (`Ctrl+F`), phím tắt, recent files.
- Phase B: thumbnail trang, tab Outlines (bookmark), chuột phải xoay/xóa trang, preview căn giữa, di chuyển box bằng mũi tên.

Kết quả tốt nhất với PDF nền trắng, có text thật. PDF scan ảnh cần OCR riêng.
