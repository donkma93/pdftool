import os


FONT_CANDIDATES = {
    "Arial": {
        "regular": r"C:\Windows\Fonts\arial.ttf",
        "bold": r"C:\Windows\Fonts\arialbd.ttf",
        "italic": r"C:\Windows\Fonts\ariali.ttf",
        "bold_italic": r"C:\Windows\Fonts\arialbi.ttf",
    },
    "Calibri": {
        "regular": r"C:\Windows\Fonts\calibri.ttf",
        "bold": r"C:\Windows\Fonts\calibrib.ttf",
        "italic": r"C:\Windows\Fonts\calibrii.ttf",
        "bold_italic": r"C:\Windows\Fonts\calibriz.ttf",
    },
    "Segoe UI": {
        "regular": r"C:\Windows\Fonts\segoeui.ttf",
        "bold": r"C:\Windows\Fonts\segoeuib.ttf",
        "italic": r"C:\Windows\Fonts\segoeuii.ttf",
        "bold_italic": r"C:\Windows\Fonts\segoeuiz.ttf",
    },
    "Tahoma": {
        "regular": r"C:\Windows\Fonts\tahoma.ttf",
        "bold": r"C:\Windows\Fonts\tahomabd.ttf",
    },
    "Times New Roman": {
        "regular": r"C:\Windows\Fonts\times.ttf",
        "bold": r"C:\Windows\Fonts\timesbd.ttf",
        "italic": r"C:\Windows\Fonts\timesi.ttf",
        "bold_italic": r"C:\Windows\Fonts\timesbi.ttf",
    },
    "Courier New": {
        "regular": r"C:\Windows\Fonts\cour.ttf",
        "bold": r"C:\Windows\Fonts\courbd.ttf",
        "italic": r"C:\Windows\Fonts\couri.ttf",
        "bold_italic": r"C:\Windows\Fonts\courbi.ttf",
    },
    "Verdana": {
        "regular": r"C:\Windows\Fonts\verdana.ttf",
        "bold": r"C:\Windows\Fonts\verdanab.ttf",
        "italic": r"C:\Windows\Fonts\verdanai.ttf",
        "bold_italic": r"C:\Windows\Fonts\verdanaz.ttf",
    },
}


def available_font_files() -> dict[str, str]:
    return {name: styles["regular"] for name, styles in FONT_CANDIDATES.items() if os.path.exists(styles["regular"])}


def find_unicode_font(font_files: dict[str, str]) -> str | None:
    return next(iter(font_files.values()), None)


def font_file_for_style(font_files: dict[str, str], family: str, bold: bool, italic: bool) -> str | None:
    style_key = "bold_italic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    style_path = FONT_CANDIDATES.get(family, {}).get(style_key)
    if style_path and os.path.exists(style_path):
        return style_path
    return font_files.get(family) or find_unicode_font(font_files)


def resolve_font_family(pdf_font_name: str | None, available: dict[str, str] | None = None) -> str:
    """Map a PDF internal font name to a known system family used by the editor."""
    available = available or available_font_files()
    if not pdf_font_name:
        return next(iter(available), "Arial")

    raw = pdf_font_name.strip()
    lowered = raw.lower().replace(" ", "")

    # Strip subset prefix like "ABCDEF+TimesNewRomanPSMT"
    if "+" in raw:
        raw = raw.split("+", 1)[1]
        lowered = raw.lower().replace(" ", "")

    hints = [
        ("timesnewroman", "Times New Roman"),
        ("times", "Times New Roman"),
        ("arial", "Arial"),
        ("helvetica", "Arial"),
        ("calibri", "Calibri"),
        ("segoeui", "Segoe UI"),
        ("segoe", "Segoe UI"),
        ("tahoma", "Tahoma"),
        ("verdana", "Verdana"),
        ("couriernew", "Courier New"),
        ("courier", "Courier New"),
        ("garamond", "Times New Roman"),
        ("georgia", "Times New Roman"),
        ("cambria", "Calibri"),
        ("comic", "Arial"),
        ("msansserif", "Arial"),
        ("nimbus", "Arial"),
        ("liberation", "Arial"),
        ("dejavu", "Arial"),
    ]
    for token, family in hints:
        if token in lowered and family in available:
            return family

    for family in available:
        if family.lower().replace(" ", "") in lowered:
            return family

    return next(iter(available), "Arial")


def font_name_suggests_bold(pdf_font_name: str | None) -> bool:
    if not pdf_font_name:
        return False
    name = pdf_font_name.lower()
    return any(token in name for token in ("bold", "black", "heavy", "semibold", "demibold"))


def font_name_suggests_italic(pdf_font_name: str | None) -> bool:
    if not pdf_font_name:
        return False
    name = pdf_font_name.lower()
    return any(token in name for token in ("italic", "oblique", "kursiv"))
