# HTTP API 参考

默认地址：`http://<服务器IP>:8765`
鉴权：启动时 `--token` 或 `PDF2MD_TOKEN`；请求头 `Authorization: Bearer <token>` 或 `X-API-Token: <token>`。

---

## GET /health

```json
{"status":"ok","version":"2.0.2","running":0,"jobs_total":12,"time":1792934474}
```

## GET /capabilities

**调用转换前必须先看这个。**

```json
{
  "service": "pdf2md-eia",
  "version": "2.0.2",
  "gpu": {
    "hardware": true,
    "name": "NVIDIA GeForce RTX 4090",
    "vram_gb": 24.0,
    "driver": "5xx.xx",
    "torch_cuda": true,
    "engine": "lmdeploy",
    "backend_ready": true,
    "device": "cuda",
    "small_backend": "torch",
    "vlm_engine": "lmdeploy",
    "note": "有独显且 CUDA 后端就绪，优先走 GPU"
  },
  "recommended": {"device": "cuda", "tier": "standard", "note": "..."},
  "params": {"device": ["auto","cpu","cuda"], "tier": ["basic","standard","advanced"], "pages": "all 或 1-5,8（仅 PDF）", "formats": ["pdf","docx","doc","xlsx","xls"]},
  "quality_note": "表格合并单元格用 HTML rowspan/colspan 保留；需要严格字段抽取可要求 1-2 页抽检。"
}
```

决策规则：

- `gpu.backend_ready=true` → `device="cuda"`, `tier="standard"`。
- `gpu.hardware=true` 但 `backend_ready=false` → `device="cpu"`，提示用户装 `mineru[full]`。
- `gpu.hardware=false` → `device="cpu"`, `tier="basic"`。

## POST /convert

### 方式 A：服务器本地路径（推荐内网）

```json
{
  "path": "D:/报告/某项目.pdf",
  "out_dir": "D:/环评md/某项目",
  "device": "auto",
  "tier": "basic",
  "pages": "all"
}
```

响应 `202`：

```json
{"job_id":"6df3ea734776","status":"queued","poll":"/jobs/6df3ea734776"}
```

### 方式 B：multipart 上传

```bash
curl -X POST http://127.0.0.1:8765/convert \
  -F "file=@某项目.pdf" \
  -F "device=auto" \
  -F "tier=basic" \
  -F "pages=all"
```

字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `path` | A 方式必填 | 服务器本地 PDF 绝对路径 |
| `file` | B 方式必填 | multipart 文件 |
| `out_dir` | 否 | 输出目录；不传则用 `PDF2MD_OUT/<job_id>` |
| `device` | 否 | `auto`（默认）/`cpu`/`cuda` |
| `tier` | 否 | `basic`（默认）/`standard`/`advanced` |
| `pages` | 否 | `all` 或 `1-5,8`，仅 PDF；Office 忽略 |
| 格式 | 否 | 支持 `pdf` / `docx` / `doc` / `xlsx` / `xls`；旧版 DOC/XLS 需 LibreOffice |

> `device="cuda"` 只在 `/capabilities` 的 `gpu.backend_ready=true` 时才会真的走 GPU；
> 否则 MinerU 会回退或在启动时报错。客户端应先检查 capabilities。

## GET /jobs/<job_id>

```json
{
  "job_id": "6df3ea734776",
  "status": "done",
  "pdf": "D:\\报告\\某项目.pdf",
  "md_path": "D:\\环评md\\某项目\\某项目.md",
  "qa_path": "D:\\环评md\\某项目\\某项目_qa.json",
  "device": "auto",
  "tier": "basic",
  "pages": "all",
  "created_at": 1792933081.5,
  "started_at": 1792933081.5,
  "finished_at": 1792933091.3,
  "qa": {
    "pages": 218,
    "parsed_pages": 218,
    "chars": 266700,
    "tables_mid": 146,
    "tables_md": 150,
    "avg_page_recall": 0.9967,
    "min_page_recall": 0.9665,
    "pages_below_0.9": 0,
    "suspicious_cells": 22,
    "pua_left": 0,
    "repaired_pages": [31,32,36,100,151,161],
    "native_pages": [101],
    "recovered_pages": [11]
  }
}
```

`status`：`queued` / `running` / `done` / `failed`。
`running` 时额外返回 `elapsed_sec`。

## GET /jobs/<job_id>/result

```json
{
  "job_id": "6df3ea734776",
  "markdown": "<!-- p1 -->\n\n...",
  "truncated": false,
  "chars": 266700,
  "md_path": "D:\\环评md\\...\\某项目.md",
  "qa": {...}
}
```

大文件建议加 `?max_chars=200000`，或直接用 download。

## GET /jobs/<job_id>/download

返回 md 文件（`Content-Disposition: attachment`）。

## GET /jobs

最近 50 个任务。

## GET /openapi.json

最小 OpenAPI 3.0 描述，方便其他 AI 自动发现接口。

---

## 错误码

| HTTP | 含义 |
|---|---|
| 400 | 参数不对（缺 path、非 PDF、device/tier 非法） |
| 401 | token 不对 |
| 404 | 文件/任务不存在 |
| 409 | 任务未完成就取 result |
| 500 | 转换内部错误（看任务里的 `error`） |

---

## Python 客户端

见 `scripts/pdf2md_client.py`：

```bash
export PDF2MD_URL=http://127.0.0.1:8765
python scripts/pdf2md_client.py capabilities
python scripts/pdf2md_client.py convert --path "D:/报告/某项目.pdf" --device auto
python scripts/pdf2md_client.py convert --file "某项目.pdf" --tier standard
```

它会自动：

1. 先查 capabilities；
2. 如果 `backend_ready=true` 且用户没显式指定，自动用 `device=cuda`；
3. 提交、轮询、下载 md 和 qa。

---

## 安全边界

- 默认只监听 `127.0.0.1`；要跨机器用 `--host 0.0.0.0`，并**设置 `--token`**。
- `path` 模式能读取服务器上运行账户可访问的任意 PDF；不要对公网开放。
- 上传文件保存在 `PDF2MD_OUT/_uploads/<job_id>/`，任务结束后按需清理。
- 只在内网/可信网络使用；跨网段时在防火墙限制来源 IP。
- 不要把生产密钥写进客户端脚本；用 `PDF2MD_TOKEN` 环境变量。
