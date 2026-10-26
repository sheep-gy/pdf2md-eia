# -*- coding: utf-8 -*-
"""隐藏子命令：单个 PDF 转换，进度写 progress.jsonl，结果写 result.json。

由 GUI 以 `EIA-report-to-md.exe _job <job.json>` 启动，不读 stdin、不打印、不暂停。
"""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 窗口化 exe 的 _job 子进程没有 stdio，用 devnull 兜底，避免第三方库写日志出错
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

_T0 = time.time()
_PROGRESS = None
_LOCK = threading.Lock()


def _safe(s):
    if not isinstance(s, str):
        return s
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def emit(ev, **fields):
    if _PROGRESS is None:
        return
    rec = {"t": round(time.time() - _T0, 3), "ev": ev, **fields}
    line = _safe(json.dumps(rec, ensure_ascii=False)) + "\n"
    with _LOCK:
        with open(_PROGRESS, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()


def _heartbeat(stop):
    while not stop.wait(5):
        emit("heartbeat", elapsed=round(time.time() - _T0, 1))


def main(argv):
    if not argv:
        return 2
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    global _PROGRESS
    _PROGRESS = Path(job["progress_file"])
    _PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    result_file = Path(job["result_file"])
    result_file.parent.mkdir(parents=True, exist_ok=True)

    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(stop,), daemon=True).start()
    emit("start", pdf=job.get("pdf"), pages=job.get("pages_known"))

    result = {"ok": False}
    code = 1
    try:
        from office_convert import convert_any
        r = convert_any(
            job["pdf"], job["out_dir"],
            overwrite=bool(job.get("overwrite")),
            keep_json=bool(job.get("keep_json")),
            pages=job.get("pages", "all"),
            device=job.get("device", "cpu"),
            tier=job.get("tier", "basic"),
            tmp_dir=job.get("tmp_dir"),
            out_stem=job.get("out_stem"),
            page_workers=int(job.get("page_workers") or 1),
            cpu_threads=int(job.get("cpu_threads") or 0),
            on_event=lambda name, data: emit(name, **data),
        )
        result = {"ok": True, "result": r}
        code = 0
    except BaseException as e:
        tb = traceback.format_exc()[-4000:]
        emit("error", message=str(e)[:2000], trace=tb)
        result = {"ok": False, "error": str(e)[:2000], "trace": tb}
        code = 1
    finally:
        stop.set()
        result_file.write_text(
            _safe(json.dumps(result, ensure_ascii=False, indent=1)),
            encoding="utf-8")
        emit("exit", code=code)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
