# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

PROJECT = Path(SPECPATH)
APP_NAME = "PDF转MD"

# 随包分发的文档与 skill
DOC_DATAS = [
    (str(PROJECT / "assets" / "PDF转MD.ico"), "assets"),
    (str(PROJECT / "使用说明_GUI.txt"), "."),
    (str(PROJECT / "AI调用说明.md"), "."),
    (str(PROJECT / "AI调用示例.py"), "."),
    (str(PROJECT / "API接口.md"), "."),
    (str(PROJECT / "tools" / "uv.exe"), "tools"),
]
SKILL = PROJECT / "skill" / "pdf-to-md-eia"
if SKILL.exists():
    for _p in SKILL.rglob("*"):
        if _p.is_file():
            _rel = _p.relative_to(SKILL).parent
            DOC_DATAS.append((str(_p), str(Path("skill") / "pdf-to-md-eia" / _rel)))

a = Analysis(
    ["cli.py"],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=DOC_DATAS,
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.font", "tkinter.filedialog",
        "tkinter.messagebox", "tkinter.scrolledtext", "tkinter.simpledialog",
        "gui", "gui.app", "gui.settings", "gui.platform_win", "gui.files",
        "gui.envcheck", "gui.installer", "gui.install_wizard", "gui.jobrunner",
        "job_child", "psutil",
        "office_convert", "mammoth", "docx", "openpyxl", "bs4",
        "eia_md.render", "eia_md.table_rebuild", "eia_md.clean_table",
        "eia_md.text_extract", "eia_md.native_page", "eia_md.gpu",
    ],
    excludes=[
        "mineru", "torch", "onnxruntime", "transformers",
        "matplotlib", "pandas", "scipy", "IPython", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(PROJECT / "assets" / "PDF转MD.ico"),
    version=str(PROJECT / "assets" / "version_info.txt"),
    manifest=str(PROJECT / "assets" / "PDF转MD.manifest"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
