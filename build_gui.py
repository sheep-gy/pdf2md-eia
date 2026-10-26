# -*- coding: utf-8 -*-
"""构建 PDF转MD 桌面版（PyInstaller onedir）。"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = Path(os.environ.get("PDF2MD_PY", sys.executable))
DIST = HERE / "dist" / "PDF转MD"

def main():
    if not (HERE / "tools" / "uv.exe").exists():
        raise SystemExit("缺少 tools/uv.exe；请先放入 uv。")
    cmd = [str(PY), "-m", "PyInstaller", "--clean", "--noconfirm",
           str(HERE / "PDF转MD.spec")]
    print("运行:", " ".join(cmd))
    subprocess.run(cmd, cwd=str(HERE), check=True)
    if (HERE / "使用说明_GUI.txt").exists():
        shutil.copyfile(HERE / "使用说明_GUI.txt", DIST / "使用说明.txt")
    # PyInstaller 的 datas 会进 _internal；给人和 AI 看的文档复制到包根目录
    for name in ("AI调用说明.md", "AI调用示例.py", "API接口.md"):
        src = HERE / name
        if src.exists():
            shutil.copyfile(src, DIST / name)
    if (HERE / "skill").exists():
        shutil.copytree(HERE / "skill", DIST / "skill", dirs_exist_ok=True)
    print("完成:", DIST / "PDF转MD.exe")

if __name__ == "__main__":
    main()
