# -*- coding: utf-8 -*-
"""首次启动自检：硬件、并发建议、GPU、依赖完整性。"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import settings
from .platform_win import (CREATE_NO_WINDOW, cpu_logical, cpu_physical,
                           disk_free_gb, free_ram_gb, nvidia_info,
                           recommend_workers, total_ram_gb)


@dataclass
class CheckItem:
    key: str
    title: str
    level: str          # required | recommended | install | optional
    ok: bool
    detail: str = ""
    size_mb: int = 0
    fix: str = ""


@dataclass
class Hardware:
    cpu_logical: int = 1
    cpu_physical: int = 1
    ram_total_gb: float = 0
    ram_free_gb: float = 0
    gpu: dict | None = None
    disk_free_gb: float = 0
    recommended_workers: int = 1
    worker_reason: str = ""
    recommended_device: str = "cpu"
    gpu_note: str = ""


@dataclass
class EnvReport:
    items: list[CheckItem] = field(default_factory=list)
    hardware: Hardware = field(default_factory=Hardware)
    ready: bool = False
    checked_at: float = 0

    def by_key(self, key: str) -> CheckItem | None:
        for it in self.items:
            if it.key == key:
                return it
        return None

    @property
    def missing_required(self) -> list[CheckItem]:
        return [x for x in self.items if x.level == "required" and not x.ok]

    @property
    def missing_recommended(self) -> list[CheckItem]:
        return [x for x in self.items if x.level == "recommended" and not x.ok]


def _env(extra: dict | None = None) -> dict:
    e = os.environ.copy()
    e.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if extra:
        e.update({k: str(v) for k, v in extra.items()})
    return e


def _run(cmd: list[str], timeout: int = 30, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=env or _env(), creationflags=CREATE_NO_WINDOW)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return -1, str(e)


def locate_uv(cfg=None) -> Path | None:
    cands: list[Path] = []
    if cfg and getattr(cfg, "uv_path", ""):
        cands.append(Path(cfg.uv_path))
    cands += [
        settings.resource_dir() / "tools" / "uv.exe",
        settings.app_dir() / "tools" / "uv.exe",
        settings.data_dir() / "bin" / "uv.exe",
    ]
    which = shutil.which("uv")
    if which:
        cands.append(Path(which))
    home = Path.home()
    appdata = Path(os.environ.get("LOCALAPPDATA", home))
    cands += [
        home / ".local" / "bin" / "uv.exe",
        home / ".cargo" / "bin" / "uv.exe",
        home / ".cherrystudio" / "bin" / "uv.exe",
        appdata / "Programs" / "uv" / "uv.exe",
        appdata / "Microsoft" / "WinGet" / "Links" / "uv.exe",
    ]
    for p in cands:
        try:
            if p and p.is_file():
                return p
        except Exception:
            pass
    return None


def _registry_pythons() -> list[Path]:
    out = []
    try:
        import winreg
        roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Python\PythonCore"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Python\PythonCore"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Python\PythonCore"),
        ]
        for hive, base in roots:
            try:
                with winreg.OpenKey(hive, base) as k:
                    n = winreg.QueryInfoKey(k)[0]
                    for i in range(n):
                        ver = winreg.EnumKey(k, i)
                        if not ver.startswith(("3.10", "3.11", "3.12")):
                            continue
                        try:
                            with winreg.OpenKey(k, ver + r"\InstallPath") as kp:
                                exe = Path(winreg.QueryValue(kp, "")) / "python.exe"
                                if exe.is_file():
                                    out.append(exe)
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass
    return out


def find_python() -> dict | None:
    """找一个可用于创建 MinerU 环境的系统 Python。"""
    # 1) py 启动器
    for ver in ("3.12", "3.11", "3.10"):
        rc, out = _run(["py", f"-{ver}", "-c", "import sys;print(sys.executable)"], 15)
        if rc == 0 and out:
            return {"path": Path(out.splitlines()[-1].strip()), "version": ver,
                    "source": f"py -{ver}"}
    # 2) 注册表
    for p in _registry_pythons():
        rc, out = _run([str(p), "-c",
                        "import sys;print('%d.%d' % sys.version_info[:2])"], 15)
        if rc == 0 and out.strip() in ("3.10", "3.11", "3.12"):
            return {"path": p, "version": out.strip(), "source": "注册表"}
    # 3) 常见路径 / PATH
    cands = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "python.exe",
        Path(r"C:\Python312\python.exe"), Path(r"C:\Python311\python.exe"),
        Path(shutil.which("python") or ""),
    ]
    for p in cands:
        if p and p.is_file():
            rc, out = _run([str(p), "-c",
                            "import sys;print('%d.%d' % sys.version_info[:2])"], 15)
            if rc == 0 and out.strip() in ("3.10", "3.11", "3.12"):
                return {"path": p, "version": out.strip(), "source": "系统 PATH"}
    return None


def hardware_report(cfg) -> Hardware:
    log = cpu_logical()
    phys = cpu_physical()
    total_ram = total_ram_gb()
    free_ram = free_ram_gb()
    gpu = nvidia_info()
    disk = disk_free_gb(cfg.env_dir if Path(cfg.env_dir).exists() else cfg.env_dir)
    workers, reason = recommend_workers(free_ram, phys)
    if gpu and gpu.get("vram_gb", 0) >= 8:
        device = "cuda"
        note = (f'检测到 {gpu["name"]}（{gpu["vram_gb"]} GB 显存），'
                f"建议使用独显；需安装 CUDA 后端")
    elif gpu:
        device = "cpu"
        note = (f'检测到 {gpu["name"]}，但显存只有 {gpu["vram_gb"]} GB，'
                f"建议仍用 CPU")
    else:
        device = "cpu"
        note = "未检测到 NVIDIA 独显，使用 CPU"
    return Hardware(
        cpu_logical=log, cpu_physical=phys,
        ram_total_gb=total_ram, ram_free_gb=free_ram,
        gpu=gpu, disk_free_gb=disk,
        recommended_workers=workers, worker_reason=reason,
        recommended_device=device, gpu_note=note,
    )


def quick_check(cfg) -> tuple[bool, str]:
    """<200ms 的粗略检查，用于启动横幅。"""
    env_py = Path(cfg.mineru_py)
    kit = Path(cfg.mineru_bin)
    home = Path(cfg.mineru_home)
    if not env_py.is_file():
        return False, "未找到 MinerU Python 环境"
    if not kit.is_file():
        return False, "未找到 mineru-kit"
    if not (home / "models").is_dir():
        return False, "未找到模型目录"
    return True, "环境路径正常"


def check_environment(cfg, deep_gpu: bool = False) -> EnvReport:
    items: list[CheckItem] = []
    hw = hardware_report(cfg)

    env_py = Path(cfg.mineru_py)
    kit = Path(cfg.mineru_bin)
    home = Path(cfg.mineru_home)

    # 1. Python + mineru 包
    if not env_py.is_file():
        items.append(CheckItem("mineru_py", "MinerU Python 环境", "required", False,
                               f"未找到 {env_py}", 800, "自动创建 Python 3.12 环境"))
    else:
        code = ("import sys, importlib.metadata as m;"
                "print(sys.version.split()[0]);"
                "print(m.version('mineru'))")
        rc, out = _run([str(env_py), "-X", "utf8", "-c", code], 25)
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        ver = lines[-1] if len(lines) >= 2 else ""
        if rc == 0 and ver:
            items.append(CheckItem("mineru_py", "MinerU Python 环境", "required", True,
                                   f"Python {lines[-2] if len(lines)>=2 else ''} · mineru {ver}"))
        else:
            items.append(CheckItem("mineru_py", "MinerU Python 环境", "required", False,
                                   f"环境存在但不可用：{out[-200:]}", 800,
                                   "修复/重装 Python 环境"))

    # 2. mineru-kit
    if not kit.is_file():
        items.append(CheckItem("mineru_kit", "MinerU 命令行工具", "required", False,
                               f"未找到 {kit}", 0, "随 MinerU 包一起安装"))
    else:
        rc, out = _run([str(kit), "version"], 20)
        ok = rc == 0
        items.append(CheckItem("mineru_kit", "MinerU 命令行工具", "required", ok,
                               (out.splitlines()[0] if out else "")[:120] if ok
                               else f"不可用：{out[-160:]}", 0, "修复环境"))

    # 3. 模型
    base_env = _env({"MINERU_HOME": str(home)})
    checks = [
        ("models_basic", "基础模型（版面/表格/OCR）", "required",
         ["--tier", "basic", "--small-backend", "onnx"], 820),
        ("models_standard", "增强补漏模型（VLM）", "recommended",
         ["--tier", "standard", "--small-backend", "onnx", "--vlm-engine", "llama-cpp"], 1200),
    ]
    for key, title, level, extra, size in checks:
        if not kit.is_file():
            items.append(CheckItem(key, title, level, False, "需要先安装 MinerU", size,
                                   "自动下载"))
            continue
        cmd = [str(kit), "models", "verify", *extra]
        rc, out = _run(cmd, 30, env=base_env)
        ok = rc == 0
        items.append(CheckItem(key, title, level, ok,
                               (out.splitlines()[-1] if out else "")[:160] if ok
                               else f"未就绪：{out[-160:]}", size,
                               "自动下载" if not ok else ""))

    # 4. 安装前置条件
    uv = locate_uv(cfg)
    items.append(CheckItem("uv", "包管理工具 uv", "install", bool(uv),
                           str(uv) if uv else "未找到，将自动下载", 35,
                           "自动下载/使用 winget"))
    need_gb = 5.0 if not items[0].ok else 1.0
    disk_ok = hw.disk_free_gb >= need_gb
    items.append(CheckItem("disk", "磁盘空间", "install", disk_ok,
                           f"{hw.disk_free_gb} GB 可用（需要约 {need_gb:.0f} GB）",
                           0, "更换安装目录"))
    try:
        target = Path(cfg.env_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        probe = target.parent / ".pdf2md_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        write_ok, write_detail = True, "可写"
    except Exception as e:
        write_ok, write_detail = False, f"不可写：{e}"
    items.append(CheckItem("write", "安装目录权限", "install", write_ok,
                           write_detail, 0, "更换到用户目录"))

    # 5. GPU（可选）
    if hw.gpu:
        backend = "未检查"
        if deep_gpu and env_py.is_file():
            code = ("import torch;print('torch',torch.cuda.is_available());"
                    "import lmdeploy;print('lmdeploy')")
            rc, out = _run([str(env_py), "-X", "utf8", "-c", code], 40)
            backend = "就绪" if (rc == 0 and "torch True" in out) else "未安装/不可用"
        items.append(CheckItem("gpu", "NVIDIA 独显", "optional", True,
                               f'{hw.gpu["name"]} · {hw.gpu["vram_gb"]} GB · 后端{backend}',
                               3500, "可安装 CUDA 后端"))
    else:
        items.append(CheckItem("gpu", "NVIDIA 独显", "optional", False,
                               "未检测到，使用 CPU", 0, ""))

    try:
        from office_convert import find_soffice
        soffice = find_soffice()
    except Exception:
        soffice = None
    items.append(CheckItem(
        "libreoffice", "LibreOffice（旧版 DOC/XLS）", "optional", bool(soffice),
        str(soffice) if soffice else "未安装；只影响 .doc/.xls，DOCX/XLSX 不受影响",
        350, "自动安装" if not soffice else ""))

    ready = all(x.ok for x in items if x.level == "required")
    return EnvReport(items=items, hardware=hw, ready=ready, checked_at=time.time())


def estimate_mb(need_basic: bool, need_standard: bool, need_gpu: bool,
                need_env: bool, need_legacy: bool = False) -> int:
    mb = 0
    if need_env:
        mb += 800
    if need_basic:
        mb += 820
    if need_standard:
        mb += 1200
    if need_gpu:
        mb += 3500
    if need_legacy:
        mb += 350
    return mb


def manual_install_text(cfg) -> str:
    return f"""手动安装（可选）

1. 安装 uv（如果系统没有）：
   https://github.com/astral-sh/uv/releases

2. 创建 MinerU 环境：
   uv venv --python 3.12 "{cfg.env_dir}"
   uv pip install --python "{cfg.mineru_py}" "mineru>=4.0,<5" pymupdf pdfplumber -i https://mirrors.aliyun.com/pypi/simple

3. 下载模型：
   set MINERU_HOME={cfg.mineru_home}
   "{cfg.mineru_bin}" models download --tier basic --small-backend onnx --source modelscope
   "{cfg.mineru_bin}" models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source modelscope

4. 校验：
   "{cfg.mineru_bin}" models verify --tier basic --small-backend onnx

5. 旧版 DOC/XLS 支持（可选）：
   winget install --id TheDocumentFoundation.LibreOffice -e

装好后回到本程序，点“环境检查”即可。
"""
