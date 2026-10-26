# -*- coding: utf-8 -*-
"""PDF转MD API 客户端（给其他 AI / 其他电脑用）。

用法：
  set PDF2MD_URL=http://192.168.1.10:8765
  python pdf2md_client.py capabilities
  python pdf2md_client.py convert --path "D:/报告/某项目.pdf" --out ./out
  python pdf2md_client.py convert --file "某项目.pdf" --out ./out

依赖：标准库即可；上传文件需要 requests（pip install requests）。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PDF2MD_URL", "http://127.0.0.1:8765").rstrip("/")
TOKEN = os.environ.get("PDF2MD_TOKEN", "")


def _headers(extra=None):
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    if extra:
        h.update(extra)
    return h


def get_json(path):
    req = urllib.request.Request(BASE + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace"))


def upload(path, fields):
    try:
        import requests
    except ImportError:
        print("上传文件需要 requests：pip install requests", file=sys.stderr)
        sys.exit(2)
    headers = {}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    with open(path, "rb") as fh:
        r = requests.post(BASE + "/convert", headers=headers,
                          files={"file": (os.path.basename(path), fh)},
                          data=fields, timeout=120)
    return r.status_code, r.json()


def download(job_id, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    job = get_json(f"/jobs/{job_id}")
    fn = os.path.basename(job.get("md_path") or f"{job_id}.md")
    url = f"{BASE}/jobs/{job_id}/download"
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=300) as r:
        path = os.path.join(out_dir, fn)
        with open(path, "wb") as fh:
            fh.write(r.read())
    qa_path = os.path.splitext(path)[0] + "_qa.json"
    with open(qa_path, "w", encoding="utf-8") as fh:
        json.dump(job.get("qa") or {}, fh, ensure_ascii=False, indent=2)
    return path, qa_path


def wait(job_id, interval=3, timeout=6 * 3600):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        j = get_json(f"/jobs/{job_id}")
        st = j.get("status")
        if st != last:
            print(f"[{int(time.time()-t0)}s] {st}", flush=True)
            last = st
        if st in ("done", "failed"):
            return j
        time.sleep(interval)
    raise TimeoutError("等待超时")


def cmd_capabilities(args):
    caps = get_json("/capabilities")
    print(json.dumps(caps, ensure_ascii=False, indent=2))
    return caps


def cmd_convert(args):
    caps = get_json("/capabilities")
    rec = caps.get("recommended") or {}
    device = args.device or rec.get("device") or "cpu"
    tier = args.tier or rec.get("tier") or "basic"
    g = caps.get("gpu") or {}
    if device == "cuda" and not g.get("backend_ready"):
        print("提示：capabilities 显示 GPU 后端未就绪，已改为 cpu。", file=sys.stderr)
        device = "cpu"
        tier = "basic"
    print(f"device={device} tier={tier}  ({g.get('note','')})", flush=True)

    fields = {"device": device, "tier": tier, "pages": args.pages}
    if args.path:
        payload = dict(fields, path=args.path)
        if args.out_dir:
            payload["out_dir"] = args.out_dir
        code, resp = post_json("/convert", payload)
    else:
        code, resp = upload(args.file, fields)
    if code not in (200, 202):
        print(json.dumps(resp, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    jid = resp["job_id"]
    print(f"job_id={jid}  {resp.get('status')}", flush=True)
    j = wait(jid)
    if j.get("status") == "failed":
        print(json.dumps(j, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    md, qa = download(jid, args.out)
    print(f"md: {md}")
    print(f"qa: {qa}")
    q = j.get("qa") or {}
    print(f"pages={q.get('pages')} chars={q.get('chars')} "
          f"avg_recall={q.get('avg_page_recall')} min_recall={q.get('min_page_recall')} "
          f"below_0.9={q.get('pages_below_0.9')}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capabilities", help="查看 GPU 与推荐参数")
    p.set_defaults(fn=cmd_capabilities)

    p = sub.add_parser("convert", help="提交并等待转换")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--path", help="服务器本地 PDF 路径")
    g.add_argument("--file", help="本机 PDF，上传到服务器")
    p.add_argument("--out", default="./pdf2md_out", help="下载目录")
    p.add_argument("--out-dir", default="", help="服务器输出目录（path 模式）")
    p.add_argument("--device", default="", choices=["", "auto", "cpu", "cuda"])
    p.add_argument("--tier", default="", choices=["", "basic", "standard", "advanced"])
    p.add_argument("--pages", default="all")
    p.set_defaults(fn=cmd_convert)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
