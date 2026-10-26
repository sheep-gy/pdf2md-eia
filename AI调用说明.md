# 给 AI / 自动化程序的调用说明

> 本文件是 `PDF转MD_桌面版` 的 AI 入口。  
> 更完整的技能说明在：`skill\pdf-to-md-eia\SKILL.md`  
> 接口字段在：`API接口.md` 和 `skill\pdf-to-md-eia\references\api.md`

---

## 0. 先读什么

1. `skill\pdf-to-md-eia\SKILL.md` —— 触发条件、GPU 判断、质量红线。
2. `skill\pdf-to-md-eia\references\api.md` —— 所有 HTTP 字段、错误码。
3. `skill\pdf-to-md-eia\references\gpu.md` —— CPU/GPU 选择与显存要求。
4. 本文件 —— 最短调用步骤。

配套脚本：

- `skill\pdf-to-md-eia\scripts\pdf2md_client.py` —— Python 客户端
- `AI调用示例.py` —— 本目录下的可直接运行示例
- `skill\pdf-to-md-eia\scripts\expand_tables.py` —— 把 HTML 合并表展开成 TSV

---

## 1. 两种使用方式

| 使用者 | 方式 |
|---|---|
| 人 | 双击 `PDF转MD.exe`，用图形界面 |
| AI / 自动化 | 带参数运行 `PDF转MD.exe`，或启动 HTTP 服务 |

双击默认进 GUI；带参数运行仍然是命令行。exe 是 GUI 子系统，不会弹黑框；
命令行模式会自动附加到当前控制台，输出正常。

---

## 2. 启动 HTTP 服务（推荐给其他电脑的 AI）

在装了 MinerU 的机器上：

```bat
PDF转MD.exe serve --host 0.0.0.0 --port 8765 --token 你的密钥
```

- 内网使用也必须设 `--token`；请求头：
  `Authorization: Bearer 你的密钥` 或 `X-API-Token: 你的密钥`
- Windows 防火墙首次会询问，选择“允许专用网络”。
- 只建议在内网/可信网络使用；不要把 8765 直接暴露到公网。

---

## 3. 最短调用流程

### 3.1 必须先看能力

```bash
curl -s http://<服务器IP>:8765/capabilities
```

重点字段：

| 返回 | 传参 |
|---|---|
| `gpu.backend_ready=true` | `device="cuda"`, `tier="standard"` |
| `gpu.hardware=true` 但 `backend_ready=false` | `device="cpu"`, `tier="basic"`；需要 GPU 时让用户跑一次 GUI 自动装 CUDA |
| `gpu.hardware=false` | `device="cpu"`, `tier="basic"` |

不要只凭“有显卡”就传 `cuda`。

### 3.2 提交转换（服务器本地路径）

```bash
curl -s -X POST http://<服务器IP>:8765/convert \
  -H "Authorization: Bearer 你的密钥" \
  -H "Content-Type: application/json" \
  -d '{"path":"D:/报告/某项目.pdf","device":"auto","tier":"basic"}'
```

返回 `job_id`。

### 3.3 上传文件（客户端没有服务器路径权限时）

```bash
curl -s -X POST http://<服务器IP>:8765/convert \
  -H "Authorization: Bearer 你的密钥" \
  -F "file=@某项目.pdf" -F "device=cpu" -F "tier=basic"
```

### 3.4 轮询与取结果

```bash
curl -s http://<服务器IP>:8765/jobs/<job_id>
curl -s -o result.md http://<服务器IP>:8765/jobs/<job_id>/download
curl -s "http://<服务器IP>:8765/jobs/<job_id>/result?max_chars=200000"
```

`/jobs/<id>` 的 `status`：`queued` / `running` / `done` / `failed`。
完成后 `qa` 字段有平均/最低页覆盖率、表格数等，先看 QA 再决定是否采用。

---

## 4. 命令行（不启服务，单机批量）

```bat
PDF转MD.exe capabilities
PDF转MD.exe version

PDF转MD.exe convert "某报告.pdf" -o "输出目录" --device auto --tier basic
PDF转MD.exe batch "D:\下载\各省环评报告" -o "D:\环评md" --pattern 报告书 --workers 2 --device auto
```

- `capabilities` 永远是第一步。
- `convert` 输出 `<文件名>.md` + `<文件名>_qa.json`。
- `batch` 保留原目录层级，可断点续跑。
- 不要调用内部命令 `_job`；那是 GUI 用的。

---

## 5. Python 客户端

```python
from pdf2md_client import get_json, post_json, wait, download

caps = get_json("/capabilities")
rec = caps["recommended"]
code, resp = post_json("/convert", {
    "path": r"D:\报告\某项目.pdf",
    "device": rec["device"],
    "tier": rec["tier"],
})
job = wait(resp["job_id"])
md, qa = download(job["job_id"], r"D:\输出")
print(md, qa)
```

脚本位置：`skill\pdf-to-md-eia\scripts\pdf2md_client.py`
或本目录 `AI调用示例.py`。

环境变量：

```bat
set PDF2MD_URL=http://127.0.0.1:8765
set PDF2MD_TOKEN=你的密钥
```

---

## 6. 支持的文件格式

| 格式 | 说明 |
|---|---|
| `.pdf` | 原有高质量链路：MinerU + 表格重建 + 漏字页补跑 |
| `.docx` | 原生解析，保留标题/段落/表格；不需要 MinerU |
| `.xlsx` | 按工作表转 Markdown 表；合并单元格填入左上角值 |
| `.doc` / `.xls` | 旧版格式，需要 LibreOffice；首次转换时 GUI 会提示自动安装（约 350 MB） |

- `pages` 参数只对 PDF 有意义；Office 文件忽略。
- `<20 页跳过` 只对 PDF 生效。
- Office 的 QA 里 `pages=0`，`tables_md`、`sheets` 等字段仍然有效。

## 7. 输出与质量

每份 PDF 生成：

- `<文件名>.md`：正文，带 `<!-- p123 -->` 页码锚点；复杂表格用 HTML `rowspan/colspan`
- `<文件名>_qa.json`：质量报告
  - `avg_page_recall` / `min_page_recall`：平均/最低页覆盖率
  - `pages_below_0.9`：低于 0.9 的页数
  - `tables_md`：表格数
  - `pua_left`：私用区乱码残留，应为 0

给 AI 读取时建议：

1. 先看 `min_page_recall`，低于 0.90 的页要求人工抽检。
2. 需要严格字段抽取时，把 HTML 表用 `expand_tables.py` 展开成 TSV，避免合并单元格歧义。
3. 不要把整份 500 页报告一次性塞进上下文；按章节/页码切片。

---

## 8. 第一次在新机器上部署

推荐让用户双击 `PDF转MD.exe`，首次运行会：

1. 自检 CPU/内存/显卡/磁盘；
2. 计算推荐并发；
3. 列出缺少的依赖和下载大小；
4. 用户选一个安装目录（支持中文路径），点“开始安装并配置”；
5. 自动下载安装 uv、Python 3.12、MinerU、基础/增强模型；
   检测到 NVIDIA 显卡时自动安装 CUDA 后端；
6. 显示“正在下载/已下载多少/正在安装”的进度，全部完成后弹窗。

如果服务端是无界面的服务器，可以让用户先在桌面跑一次 GUI 完成安装，
之后再只用 `PDF转MD.exe serve`；或者按
`skill\pdf-to-md-eia\references\installation.md` 手动安装。

**离线使用**：转换本身完全离线；只有首次安装依赖需要联网。
离线部署时，建议在联网机器上把安装目录设成与离线机器相同的路径
（例如都用 `D:\PDF2MD_runtime`），装好 MinerU、模型、LibreOffice/CUDA 后，
把 `PDF转MD_桌面版` 文件夹和整个运行时目录按原路径拷过去。
venv 脚本记录绝对路径，换路径可能失效；不要只拷 exe。

---

## 9. 常见错误

| 现象 | 处理 |
|---|---|
| 401 / 403 | 没带 token 或 token 不对 |
| `path not found` / 无法读取 | `path` 必须是服务器本机可访问的绝对路径；否则用 multipart 上传 |
| `quality_tier_unavailable` | 模型没下全，或 `MINERU_HOME` 不对 |
| `backend_ready=false` | 不要传 `cuda`；先传 `cpu`，或让用户在 GUI 里自动装 CUDA |
| 转换很慢 | 扫描件/复杂表正常；basic 约 3–4 秒/页，standard 更慢 |
| 中文路径报 Unicode | 已支持中文路径；极少数引擎仍报错时，把环境/模型换到英文路径 |

---

## 10. 给 AI 的硬性规则

1. **先 `GET /capabilities`**，再决定 `device` / `tier`。
2. 不要在 `backend_ready=false` 时声称用了 GPU。
3. 不要跳过 QA；`min_page_recall < 0.90` 要提示抽检。
4. 不要把客户 PDF 发到未授权的第三方服务；本程序默认全程本地处理。
5. 不要在公网暴露 `serve`；必须设 token。
6. 不要调用内部 `_job` 子命令，除非你在改这个程序本身。


---

## 使用声明

- 软件开发者：**sheepgao**
- 允许个人免费使用，**商用请联系开发者**
- 许可证：PolyForm Noncommercial 1.0.0；Required Notice: Copyright 2026 sheepgao
