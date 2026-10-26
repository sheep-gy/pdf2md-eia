# -*- coding: utf-8 -*-
"""EIA report to md 统一入口（CLI + API）。

用法：
  EIA-report-to-md.exe convert "某报告.pdf" -o "输出目录"
  EIA-report-to-md.exe batch "D:\\下载\\各省环评报告" -o "D:\\环评md" --pattern 报告书 --workers 2
  EIA-report-to-md.exe serve --host 0.0.0.0 --port 8765
  EIA-report-to-md.exe capabilities
  EIA-report-to-md.exe version

也可以把 PDF 直接拖到 exe 上（等同 convert）。
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def ensure_console():
    """窗口化 exe 在命令行模式下恢复 stdout/stderr。

    优先复用已有的重定向管道（支持 `> out.txt` / 其他程序调用），
    没有管道时再附加父控制台或新建控制台。
    """
    if os.name != "nt":
        return
    if sys.stdout is None:
        try:
            sys.stdout = open(1, "w", encoding="utf-8", errors="replace",
                              buffering=1, closefd=False)
        except Exception:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(2, "w", encoding="utf-8", errors="replace",
                              buffering=1, closefd=False)
        except Exception:
            pass
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        k32 = ctypes.windll.kernel32
        if not k32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            k32.AllocConsole()
        try:
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleCP(65001)
        except Exception:
            pass
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8",
                              errors="replace", buffering=1)
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8",
                              errors="replace", buffering=1)
        if sys.stdin is None:
            try:
                sys.stdin = open(0, "r", encoding="utf-8", errors="replace",
                                 closefd=False)
            except Exception:
                try:
                    sys.stdin = open("CONIN$", "r", encoding="utf-8",
                                     errors="replace")
                except Exception:
                    pass
    except Exception:
        pass


def _help():
    print(__doc__)
    print("提示：先运行 capabilities 看有没有独显；有就加 --device cuda。")


def _pause(msg):
    """窗口化/无 stdin 时不要因为 input() 崩掉。"""
    if sys.stdin is None:
        return
    try:
        input(msg)
    except (EOFError, RuntimeError, OSError):
        pass


def main():
    args = sys.argv[1:]
    if args and args[0].lower() != "_job":
        ensure_console()
    if not args:
        from gui.app import run_gui
        return run_gui()

    cmd = args[0].lower()

    if cmd == "_job":
        from job_child import main as job_main
        raise SystemExit(job_main(args[1:]))

    if cmd == "gui":
        from gui.app import run_gui
        return run_gui(initial_paths=args[1:])

    if cmd.endswith((".pdf", ".docx", ".doc", ".xlsx", ".xls")):
        argv = [sys.argv[0]] + args
        if "-o" not in args and "--out" not in args:
            pdf = os.path.abspath(args[0])
            out = os.path.join(os.path.dirname(pdf), "md输出")
            argv += ["-o", out, "--overwrite"]
        sys.argv = argv
        from convert import main as m
        m()
        if getattr(sys, "frozen", False):
            _pause("\n转换结束，按回车退出...")
        return
    if cmd in ("convert", "batch"):
        sys.argv = [sys.argv[0]] + args[1:]
        if cmd == "convert":
            from convert import main as m
        else:
            from batch import main as m
        m()
        return
    if cmd in ("serve", "server", "api"):
        sys.argv = [sys.argv[0]] + args[1:]
        from server import main as m
        m()
        return
    if cmd in ("capabilities", "caps", "gpu"):
        from eia_md.gpu import detect
        print(json.dumps(detect(), ensure_ascii=False, indent=2))
        return
    if cmd in ("version", "-v", "--version"):
        from eia_md.gpu import detect
        print(json.dumps({"version": "2.0.0", "gpu": detect()},
                         ensure_ascii=False, indent=2))
        return
    if cmd in ("help", "-h", "--help"):
        _help()
        return
    print(f"未知命令: {args[0]}")
    _help()
    sys.exit(2)


if __name__ == "__main__":
    no_pause = bool(sys.argv[1:2]) and sys.argv[1].lower() == "_job"
    try:
        main()
    except SystemExit as e:
        if getattr(sys, "frozen", False) and not no_pause and e.code not in (0, None):
            _pause("\n出错了，按回车退出...")
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        if getattr(sys, "frozen", False) and not no_pause:
            _pause("\n出错了，按回车退出...")
        raise
