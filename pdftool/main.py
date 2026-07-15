import sys

from .editor_app import PdfParagraphEditor
from .single_instance import ensure_single_instance


def main():
    guard = ensure_single_instance()
    if guard is None:
        sys.exit(0)

    app = PdfParagraphEditor()
    try:
        app.mainloop()
    finally:
        # Release only after the UI loop ends (window closed or process exit).
        guard.release()


if __name__ == "__main__":
    main()
