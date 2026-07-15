import sys

from .branding import ensure_start_menu_shortcut, set_windows_app_user_model_id
from .editor_app import PdfParagraphEditor
from .single_instance import ensure_single_instance


def main():
    # Must run before any Tk window so Windows taskbar does not use python.exe icon.
    set_windows_app_user_model_id()

    guard = ensure_single_instance()
    if guard is None:
        sys.exit(0)

    # Shortcut with custom .ico — helps taskbar / Start Menu branding.
    try:
        ensure_start_menu_shortcut()
    except Exception:
        pass

    app = PdfParagraphEditor()
    try:
        app.mainloop()
    finally:
        # Release only after the UI loop ends (window closed or process exit).
        guard.release()


if __name__ == "__main__":
    main()
