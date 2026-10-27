# -*- coding: utf-8 -*-
"""PDF → AI 可读 Markdown 的本地 HTTP API。

启动：
  python server.py --host 127.0.0.1 --port 8765
  python server.py --host 0.0.0.0 --port 8765 --token 你的密钥

给其他 AI 的最短用法：
  1. GET  /capabilities       看有没有独显、推荐 device/tier
  2. POST /convert            传 path（服务器本地路径）或 multipart file
  3. GET  /jobs/<id>          轮询
  4. GET  /jobs/<id>/result   取 markdown + qa
"""
import argparse
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, request, send_file

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from office_convert import convert_any, SUPPORTED
from eia_md.gpu import detect as detect_gpu

APP_VERSION = "2.0.2"
app = Flask(__name__)
JOBS = {}
LOCK = threading.Lock()
EXECUTOR = None
CONFIG = {
    "out_root": None,
    "uploads": None,
    "token": "",
    "workers": 1,
}


def _auth_ok():
    token = CONFIG["token"]
    if not token:
        return True
    got = request.headers.get("X-API-Token", "")
    if got == token:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


def _need_auth():
    if _auth_ok():
        return None
    return jsonify({"error": "unauthorized", "hint": "带 Authorization: Bearer <token>"}), 401


def _save_job(job):
    p = Path(CONFIG["out_root"]) / "_jobs" / f"{job['job_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _update(job_id, **kw):
    with LOCK:
        j = JOBS.get(job_id)
        if not j:
            return
        j.update(kw)


def _run(job_id, pdf_path, out_dir, pages, device, tier):
    _update(job_id, status="running", started_at=time.time())
    _save_job(JOBS[job_id])
    try:
        r = convert_any(pdf_path, out_dir, overwrite=True, pages=pages,
                        device=device, tier=tier)
        qa = r.get("qa") or {}
        _update(job_id, status="done", finished_at=time.time(),
                md_path=str(Path(r["path"]).resolve()),
                qa_path=str((Path(out_dir) / (Path(pdf_path).stem + "_qa.json")).resolve()),
                qa=qa)
    except Exception as e:
        _update(job_id, status="failed", finished_at=time.time(), error=str(e)[:800])
    _save_job(JOBS[job_id])


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Token"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "pdf2md-eia",
        "version": APP_VERSION,
        "endpoints": ["/health", "/capabilities", "/convert", "/jobs/<id>",
                      "/jobs/<id>/result", "/jobs/<id>/download", "/openapi.json"],
        "hint": "先 GET /capabilities，有独显就 device=cuda，否则 device=cpu",
    })


@app.route("/health", methods=["GET"])
def health():
    with LOCK:
        running = sum(1 for j in JOBS.values() if j.get("status") in ("queued", "running"))
        total = len(JOBS)
    return jsonify({"status": "ok", "version": APP_VERSION, "running": running,
                    "jobs_total": total, "time": int(time.time())})


@app.route("/capabilities", methods=["GET"])
def capabilities():
    g = detect_gpu()
    if g["backend_ready"]:
        rec = {"device": "cuda", "tier": "standard",
               "note": "检测到可用 CUDA 后端：优先 GPU；复杂页可再提 advanced。"}
    elif g["hardware"]:
        rec = {"device": "cpu", "tier": "basic",
               "note": "有 NVIDIA 显卡但 Torch/LMDeploy/vLLM 后端未就绪：先按 CPU 跑；装 mineru[full] 后可切 cuda。"}
    else:
        rec = {"device": "cpu", "tier": "basic",
               "note": "无独显：device=cpu，tier=basic；补漏页会自动用 standard。"}
    return jsonify({
        "service": "pdf2md-eia",
        "version": APP_VERSION,
        "gpu": g,
        "recommended": rec,
        "params": {
            "device": ["auto", "cpu", "cuda"],
            "tier": ["basic", "standard", "advanced"],
            "pages": "all 或 1-5,8（仅 PDF）",
            "formats": ["pdf", "docx", "doc", "xlsx", "xls"],
        },
        "quality_note": "表格合并单元格用 HTML rowspan/colspan 保留；需要严格字段抽取可要求 1-2 页抽检。",
    })


@app.route("/openapi.json", methods=["GET"])
def openapi():
    return jsonify({
        "openapi": "3.0.0",
        "info": {"title": "PDF to AI-readable Markdown", "version": APP_VERSION},
        "paths": {
            "/capabilities": {"get": {"summary": "GPU/推荐参数"}},
            "/convert": {"post": {"summary": "提交转换任务",
                                  "requestBody": {"content": {
                                      "application/json": {"schema": {"type": "object", "properties": {
                                          "path": {"type": "string"},
                                          "out_dir": {"type": "string"},
                                          "device": {"type": "string", "enum": ["auto", "cpu", "cuda"]},
                                          "tier": {"type": "string", "enum": ["basic", "standard", "advanced"]},
                                          "pages": {"type": "string"},
                                      }}},
                                      "multipart/form-data": {"schema": {"type": "object", "properties": {
                                          "file": {"type": "string", "format": "binary"},
                                          "device": {"type": "string"},
                                          "tier": {"type": "string"},
                                          "pages": {"type": "string"},
                                      }}}}}}},
            "/jobs/{job_id}": {"get": {"summary": "任务状态"}},
            "/jobs/{job_id}/result": {"get": {"summary": "取 markdown + qa"}},
            "/jobs/{job_id}/download": {"get": {"summary": "下载 md 文件"}},
        },
    })


@app.route("/convert", methods=["POST", "OPTIONS"])
def convert():
    if request.method == "OPTIONS":
        return ("", 204)
    bad = _need_auth()
    if bad:
        return bad
    data = request.get_json(silent=True) or {}
    device = data.get("device") or request.form.get("device") or "auto"
    tier = data.get("tier") or request.form.get("tier") or "basic"
    pages = str(data.get("pages") or request.form.get("pages") or "all")
    out_dir = data.get("out_dir") or request.form.get("out_dir")

    # 上传文件
    f = request.files.get("file")
    if f is not None and f.filename:
        safe = Path(f.filename).name
        jid = uuid.uuid4().hex[:12]
        updir = Path(CONFIG["uploads"]) / jid
        updir.mkdir(parents=True, exist_ok=True)
        pdf_path = updir / safe
        f.save(str(pdf_path))
        out_dir = out_dir or str(Path(CONFIG["out_root"]) / jid)
    else:
        path = data.get("path") or request.form.get("path")
        if not path:
            return jsonify({"error": "缺少 path，或上传 multipart 的 file"}), 400
        pdf_path = Path(path)
        if not pdf_path.exists():
            return jsonify({"error": f"文件不存在: {path}"}), 404
        jid = uuid.uuid4().hex[:12]
        out_dir = out_dir or str(Path(CONFIG["out_root"]) / jid)

    if pdf_path.suffix.lower() not in SUPPORTED:
        return jsonify({"error": "只支持 PDF / DOCX / DOC / XLSX / XLS"}), 400
    if device not in ("auto", "cpu", "cuda"):
        return jsonify({"error": "device 只能是 auto/cpu/cuda"}), 400
    if tier not in ("basic", "standard", "advanced"):
        return jsonify({"error": "tier 只能是 basic/standard/advanced"}), 400

    job = {"job_id": jid, "status": "queued", "pdf": str(pdf_path),
           "out_dir": str(out_dir), "device": device, "tier": tier,
           "pages": pages, "created_at": time.time()}
    with LOCK:
        JOBS[jid] = job
    _save_job(job)
    EXECUTOR.submit(_run, jid, str(pdf_path), str(out_dir), pages, device, tier)
    return jsonify({"job_id": jid, "status": "queued",
                    "poll": f"/jobs/{jid}"}), 202


@app.route("/jobs", methods=["GET"])
def jobs():
    with LOCK:
        items = sorted(JOBS.values(), key=lambda x: x.get("created_at", 0), reverse=True)
    return jsonify({"jobs": items[:50]})


@app.route("/jobs/<job_id>", methods=["GET"])
def job(job_id):
    with LOCK:
        j = JOBS.get(job_id)
    if not j:
        return jsonify({"error": "job not found"}), 404
    out = dict(j)
    if j.get("status") == "running" and j.get("started_at"):
        out["elapsed_sec"] = round(time.time() - j["started_at"], 1)
    return jsonify(out)


@app.route("/jobs/<job_id>/result", methods=["GET"])
def result(job_id):
    with LOCK:
        j = JOBS.get(job_id)
    if not j:
        return jsonify({"error": "job not found"}), 404
    if j.get("status") != "done":
        return jsonify({"error": "job not done", "status": j.get("status")}), 409
    md_path = Path(j["md_path"])
    if not md_path.exists():
        return jsonify({"error": "md file missing"}), 500
    limit = int(request.args.get("max_chars", "0") or 0)
    text = md_path.read_text(encoding="utf-8")
    truncated = False
    if limit and len(text) > limit:
        text = text[:limit]
        truncated = True
    return jsonify({"job_id": job_id, "markdown": text, "truncated": truncated,
                    "chars": len(text), "md_path": str(md_path),
                    "qa": j.get("qa")})


@app.route("/jobs/<job_id>/download", methods=["GET"])
def download(job_id):
    with LOCK:
        j = JOBS.get(job_id)
    if not j or j.get("status") != "done":
        return jsonify({"error": "job not ready"}), 404
    return send_file(j["md_path"], as_attachment=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--token", default=os.environ.get("PDF2MD_TOKEN", ""))
    ap.add_argument("--out-root", default=os.environ.get("PDF2MD_OUT", "D:/pdf2md_out"))
    ap.add_argument("--uploads", default="")
    args = ap.parse_args()

    global EXECUTOR
    CONFIG["out_root"] = str(Path(args.out_root).resolve())
    CONFIG["uploads"] = args.uploads or str(Path(CONFIG["out_root"]) / "_uploads")
    CONFIG["token"] = args.token
    CONFIG["workers"] = args.workers
    Path(CONFIG["out_root"]).mkdir(parents=True, exist_ok=True)
    Path(CONFIG["uploads"]).mkdir(parents=True, exist_ok=True)
    EXECUTOR = ThreadPoolExecutor(max_workers=max(1, args.workers))

    g = detect_gpu()
    print(f"pdf2md-eia {APP_VERSION}", flush=True)
    print(f"GPU: {g['note']}", flush=True)
    print(f"输出目录: {CONFIG['out_root']}", flush=True)
    print(f"监听: http://{args.host}:{args.port}  (workers={args.workers})", flush=True)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
