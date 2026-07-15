# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['pdf_editor.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/pdftool.ico', 'assets'),
        ('assets/pdftool.png', 'assets'),
        ('assets/pdftool_16.png', 'assets'),
        ('assets/pdftool_32.png', 'assets'),
        ('assets/pdftool_48.png', 'assets'),
        ('assets/pdftool_64.png', 'assets'),
        ('assets/pdftool_128.png', 'assets'),
        ('assets/pdftool_256.png', 'assets'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tensorflow', 'torch', 'torchvision', 'pandas', 'scipy', 'numpy', 'matplotlib', 'cv2', 'sklearn', 'pyarrow', 'onnxruntime', 'pygame'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PDFTextEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/pdftool.ico',
)
