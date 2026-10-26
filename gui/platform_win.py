# -*- coding: utf-8 -*-
"""Windows 平台工具：无窗口子进程、控制台隐藏、DPI、硬件/磁盘、单实例。"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def hide_console_if_alone() -> None:
    """双击启动时隐藏自己的控制台；从 cmd 启动时不隐藏父窗口。"""
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return
        arr = (ctypes.c_uint * 8)()
        n = k32.GetConsoleProcessList(arr, 8)
        if n <= 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def set_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def kill_tree(pid: int) -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NO_WINDOW, timeout=30)
    except Exception:
        pass


def open_path(path: str | Path) -> None:
    p = str(path)
    try:
        os.startfile(p)  # type: ignore[attr-defined]
    except Exception:
        subprocess.Popen(["explorer", p], creationflags=CREATE_NO_WINDOW)


def _mem_status() -> tuple[float, float]:
    """返回 (total_gb, available_gb)。"""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.total / 2**30, vm.available / 2**30
    except Exception:
        pass
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys / 2**30, st.ullAvailPhys / 2**30
    except Exception:
        return 0.0, 0.0


def total_ram_gb() -> float:
    return round(_mem_status()[0], 1)


def free_ram_gb() -> float:
    return round(_mem_status()[1], 1)


def cpu_logical() -> int:
    return os.cpu_count() or 1


def cpu_physical() -> int:
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except Exception:
        pass
    return max(1, cpu_logical() // 2)


def disk_free_gb(path: str | Path) -> float:
    try:
        p = Path(path)
        while not p.exists() and p.parent != p:
            p = p.parent
        return round(shutil.disk_usage(p).free / 2**30, 1)
    except Exception:
        return 0.0


def nvidia_info() -> dict | None:
    """返回第一张 NVIDIA 卡的信息，没有则 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW)
        if p.returncode != 0:
            return None
        lines = [x for x in (p.stdout or "").strip().splitlines() if x.strip()]
        if not lines:
            return None
        best = None
        for line in lines:
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                vram = int(float(parts[1]))
            except Exception:
                vram = 0
            item = {"name": parts[0], "vram_mb": vram,
                    "vram_gb": round(vram / 1024, 1), "driver": parts[2],
                    "count": len(lines)}
            if best is None or item["vram_mb"] > best["vram_mb"]:
                best = item
        return best
    except Exception:
        return None


def recommend_workers(free_gb: float, physical_cores: int) -> tuple[int, str]:
    """按可用内存与物理核数给出并发建议（每份约 5-6 GB）。"""
    ram_n = max(1, int((free_gb - 3) // 6))
    cpu_n = max(1, physical_cores // 4)
    n = max(1, min(3, ram_n, cpu_n))
    reason = (f"可用内存 {free_gb:.1f} GB（每进程约 5–6 GB），"
              f"{physical_cores} 个物理核 → 建议 {n} 个并发")
    return n, reason


def acquire_single_instance(name: str = r"Local\PDF2MD_GUI_v2"):
    if os.name != "nt":
        return True, None
    try:
        h = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        err = ctypes.windll.kernel32.GetLastError()
        return (err != 183), h
    except Exception:
        return True, None


def activate_window(title: str) -> None:
    """把已运行的主窗口（以及可能存在的向导）恢复并带到前台。"""
    if os.name != "nt":
        return
    u = ctypes.windll.user32
    titles = [title, "PDF转MD · 首次运行自检",
              "安装 LibreOffice（旧版 DOC/XLS）"]
    seen = set()
    for t in titles:
        try:
            hwnd = u.FindWindowW(None, t)
            if not hwnd or hwnd in seen:
                continue
            seen.add(hwnd)
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, 9)   # SW_RESTORE
            else:
                u.ShowWindow(hwnd, 5)   # SW_SHOW
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        except Exception:
            pass
    restore_aux_windows()


def open_log_folder(path: Path) -> None:
    open_path(path)


def restore_aux_windows() -> None:
    """枚举所有同标题的向导/对话框窗口，把隐藏或最小化的恢复出来。"""
    if os.name != "nt":
        return
    u = ctypes.windll.user32
    titles = {"PDF转MD · 首次运行自检", "安装 LibreOffice（旧版 DOC/XLS）"}
    try:
        CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, lparam):
            try:
                n = u.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value in titles:
                        if u.IsIconic(hwnd):
                            u.ShowWindow(hwnd, 9)   # SW_RESTORE
                        elif not u.IsWindowVisible(hwnd):
                            u.ShowWindow(hwnd, 5)   # SW_SHOW
                        u.BringWindowToTop(hwnd)
            except Exception:
                pass
            return True
        u.EnumWindows(CB(_cb), 0)
    except Exception:
        pass

