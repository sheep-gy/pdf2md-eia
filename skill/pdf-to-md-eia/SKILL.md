---
name: pdf-to-md-eia
description: Convert Chinese environmental impact assessment (EIA / 环评) PDFs, DOCX, XLSX, DOC and XLS into AI-readable Markdown with correct complex tables, merged cells, formulas, scans, and page anchors. Use this skill whenever the user mentions 环评报告/报告书/报告表, 排污许可, 竣工验收, 导则/标准 PDF, PDF 转 Markdown, 表格合并单元格, 扫描件 OCR, or wants another AI/computer to call a PDF-to-Markdown conversion service. Also use it when installing or tuning MinerU, deciding CPU vs GPU, or checking whether a converted report is complete enough to feed to an LLM.
---

# 环评 PDF → AI 可读 Markdown

## 这个 skill 解决什么

环评报告里的表格经常有跨行/跨列、整页外框、跨页续表、上下标、公式和扫描件。
普通 `pdftotext` / PyMuPDF 规则提取会把正文并进表格、把多张表粘成一张，AI 读到的数据会错列。

## 0. 这个 skill 在哪里、AI 先读什么

- 本 skill 随 **PDF转MD_桌面版** 一起分发，位置：`<包目录>\skill\pdf-to-md-eia\`
- 包根目录还有：
  - `AI调用说明.md`：给其他 AI 的最短调用步骤（先读这个）
  - `AI调用示例.py`：Python 客户端示例
  - `API接口.md`：HTTP 接口字段与示例
- `references/gui.md`：桌面版首次自检、自动安装、进度说明
- 只是调用转换：启动 `PDF转MD.exe serve --host 0.0.0.0 --port 8765 --token 密钥`，
  先 `GET /capabilities`，再 `POST /convert`。
- 第一次在新机器上部署：让用户在 GUI 首次运行里点“开始安装并配置”，
  程序会自动下载 uv、Python 3.12、MinerU、基础/增强模型；检测到 NVIDIA 显卡时
  会一并安装 CUDA 后端。安装过程有下载/安装进度，全部完成后弹窗。
  不需要用户手动敲 pip / 模型下载命令。

本 skill 的固定路线：

1. **MinerU basic** 负责版面、表格区域、公式、OCR 兜底；
2. **PDF 真实线条** 重建表格网格（rowspan / colspan）；
3. **PDF 文字层** 填单元格文字（上下标不被打散、复选框不丢）；
4. 页级覆盖率体检 → 漏字页用 **standard** 重跑 → 仍漏则 **原生兜底**；
5. 清洗、压缩 token、输出页锚点与 QA。

---

## Office 文件（DOCX / XLSX / DOC / XLS）

- `.docx`：原生解析，保留标题、段落、表格（表格为 HTML，保留合并单元格），不需要 MinerU。
- `.xlsx`：按工作表转 Markdown 管道表；合并单元格会填入左上角的值。
- `.doc` / `.xls`：旧版格式，需要 LibreOffice。GUI 第一次添加这类文件时会提示自动安装
  （winget，约 350 MB）；也可以从菜单“环境 → 安装旧版 DOC/XLS 支持”触发。
- `pages` 参数和“<20 页跳过”只对 PDF 生效。
- Office 文件没有页码锚点；QA 中 `pages=0`，`tables_md`、`sheets` 等字段仍然有效。

---

## 第一步：先探测 GPU（必须做）

不要凭感觉说“有显卡就快”。先调用：

```bash
curl -s http://127.0.0.1:8765/capabilities
```

或运行：

```bash
PDF转MD.exe capabilities
python scripts/detect_gpu.py
```

读 `gpu` 和 `recommended`：

| 返回值 | 怎么传参 |
|---|---|
| `gpu.backend_ready = true` | `device="cuda"`，`tier="standard"`（复杂页可 `advanced`） |
| `gpu.hardware = true` 但 `backend_ready = false` | 先按 `device="cpu"` 跑；要提速需装 `mineru[full]` 的 Torch/LMDeploy/vLLM 后端 |
| `gpu.hardware = false` | `device="cpu"`，`tier="basic"` |

**有独显就尽量走独显**，但只在 `backend_ready=true` 时声称“已用 GPU”。
`hardware=true` 只代表机器有卡，不代表 MinerU 的 CUDA 后端装好了。

---

## 三种调用方式

### A. HTTP API（推荐给其他电脑的 AI）

服务端在装了 MinerU 的机器上跑：

```bash
PDF转MD.exe serve --host 0.0.0.0 --port 8765 --token 你的密钥
```

客户端：

```bash
# 1. 看能力
curl -s http://<服务器IP>:8765/capabilities

# 2. 提交（服务器本地路径）
curl -s -X POST http://<服务器IP>:8765/convert \
  -H "Authorization: Bearer 你的密钥" \
  -H "Content-Type: application/json" \
  -d '{"path":"D:/报告/某项目.pdf","device":"cuda","tier":"standard"}'

# 3. 轮询
curl -s http://<服务器IP>:8765/jobs/<job_id>

# 4. 取结果 / 下载
curl -s "http://<服务器IP>:8765/jobs/<job_id>/result?max_chars=200000"
curl -s -o result.md http://<服务器IP>:8765/jobs/<job_id>/download
```

也可以用 multipart 上传（适合客户端没有服务器文件系统访问权）：

```bash
curl -s -X POST http://<服务器IP>:8765/convert \
  -H "Authorization: Bearer 你的密钥" \
  -F "file=@某项目.pdf" -F "device=cuda" -F "tier=standard"
```

完整字段、错误码、Python 客户端见 `references/api.md` 和 `scripts/pdf2md_client.py`。

### B. exe 命令行

桌面位置：`PDF转MD_桌面版\PDF转MD.exe`
（exe 必须和 `_internal` 同目录；MinerU 环境/模型仍在 `D:\mineru_env`、`D:\mineru_home`）

```bat
PDF转MD.exe convert "某报告.pdf" -o "输出目录" --device auto --tier basic
PDF转MD.exe batch "D:\下载\各省环评报告" -o "D:\环评md" --pattern 报告书 --workers 2 --device auto
PDF转MD.exe serve --host 0.0.0.0 --port 8765
PDF转MD.exe capabilities
```

把 PDF 直接拖到 exe 上，等同 `convert`。

### C. Python 模块

如果本机有本项目源码：

```python
from convert import convert_one
r = convert_one("某报告.pdf", "输出目录", device="auto", tier="basic")
print(r["qa"])
```

---

## 标准工作流

1. **先体检 PDF 类型**（文字版 / 扫描件 / 混合）。
   `probe_all.py` 或转换时的页级文字层检测会自动做。
2. **提交转换**。默认 `tier="basic"`；有 GPU 可 `standard`，极复杂版面再 `advanced`。
3. **轮询任务**，不要反复提交同一份。
4. **读 QA**：
   - `min_page_recall` / `avg_page_recall`：页级字符种类覆盖率
   - `pages_below_0.9`：低于 90% 的页数
   - `repaired_pages`：standard 补跑的页
   - `native_pages`：原生兜底页
   - `recovered_pages`：整句补录页
   - `pua_left`：私用区乱码残留（应为 0）
   - `tables_mid` / `tables_md`：表格块数
5. **关键数据表抽检**：如果用户要提取监测值/审批文号/投资额等，别只看覆盖率；
   用 `scripts/expand_tables.py` 把含合并单元格的 HTML 表展开成逻辑网格，再逐项核对。
6. **交付**：md + `_qa.json`；告诉用户页锚点用法（`<!-- p123 -->`）。

### 验收红线

- `pages_below_0.9 > 0`：不能直接说“完整”，要说明哪几页需要抽检。
- `pua_left > 0`：有乱码字符，需回退/修正。
- 表格块数为 0 但 PDF 明显有表：说明版面识别失败，改用 `tier="standard"` 重跑该页。
- 扫描件：OCR 数字/单位必须人工抽检，不能承诺 100%。

---

## 合并单元格怎么读

输出里的复杂表用标准 HTML：

```html
<tr>
  <td rowspan="4" colspan="2">点位编号<br>检测项目</td>
  <td colspan="6">井场内S1</td>
  <td rowspan="4">第二类用地筛选值</td>
</tr>
```

含义：

- `点位编号/检测项目` 占 2 列 × 4 行；
- `井场内S1` 横跨 6 列；
- `第二类用地筛选值` 纵跨 4 行；
- 后续行里没有重复写这些值，是 HTML 规范，不是数据缺失。

**读表时先按 rowspan/colspan 展开成逻辑网格**，不要逐行数 `<td>`。
`scripts/expand_tables.py` 可以直接做这件事：

```bash
python scripts/expand_tables.py 某报告.md --table 5 --format tsv
python scripts/expand_tables.py 某报告.md --all --out grids/
```

如果模型不擅长 HTML 表格，优先用这个脚本展开后的 TSV，而不是硬读 HTML。

简单表（无合并）已经转成 Markdown 管道表，可直接读。
续表前会有 `<!-- 续表：表 x-y -->`，表头可能不重复，需要结合上一页。

---

## GPU 策略

| 场景 | 建议 |
|---|---|
| 无独显 | `device="cpu"`, `tier="basic"`；补漏页自动用 standard |
| 有独显、CUDA 后端就绪 | `device="cuda"`, `tier="standard"`；复杂页 `advanced` |
| 有独显、后端没装 | 先用 CPU；装 `mineru[full]` 后再切 CUDA |
| 24 GB 显存 | 1 个 VLM worker 约 13 GB；basic 可多开 |
| 16 GB 显存 | vLLM 默认配置可能 OOM；用 llama.cpp 或降 `gpu_memory_utilization` |
| Windows | GPU 路径不如 Linux/WSL2 成熟；优先 WSL2 |

大致速度（218 页样例，详见 `references/gpu.md`）：

- CPU basic + 少量补漏：**约 12.8 分钟**
- GPU basic + 补漏：**约 4–6 分钟**
- GPU standard 全页：约 4–8 分钟
- GPU 的 VLM 补漏页：比 CPU 快 **10–20 倍**

---

## 安装与环境

最短版本：

```bat
uv venv --python 3.12 D:\mineru_env
uv pip install "mineru>=4.0,<5" pymupdf pdfplumber flask --python D:\mineru_env\Scripts\python.exe -i https://mirrors.aliyun.com/pypi/simple

set MINERU_HOME=D:\mineru_home
set MINERU_MODEL_SOURCE=modelscope
D:\mineru_env\Scripts\mineru-kit.exe models download --tier basic --small-backend onnx --source modelscope
D:\mineru_env\Scripts\mineru-kit.exe models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source modelscope
```

**中文路径已支持**：输入 PDF、输出目录、Python 环境和模型目录都可以用中文，
已实测中文路径下的基础模型和 standard 补漏模型。若个别第三方引擎仍报
`No mapping for the Unicode character exists...`，再把环境/模型换到英文路径。

完整步骤、GPU 扩展、环境变量、故障排查见 `references/installation.md`。

---

## 常见故障

| 现象 | 原因 / 处理 |
|---|---|
| `No mapping for the Unicode character exists` | 极少数引擎对中文路径兼容不佳；把环境/模型换到 `D:\mineru_env` / `D:\mineru_home` 重试 |
| `quality_tier_unavailable` | 模型没下全，或 `MINERU_MODEL_SOURCE=local` 但 `MINERU_HOME` 不对 |
| 表格字序错乱 | 用本项目的表格重建；不要直接用 MinerU 原表 |
| 整页正文丢失 | 页级覆盖率会触发 standard/原生兜底；检查 `repaired_pages` |
| 续表没表头 | 看 `<!-- 续表：表 x-y -->`；必要时结合上一页 |
| 扫描件数字错 | OCR 局限；关键数值必须人工抽检 |
| GPU 没生效 | `/capabilities` 的 `backend_ready` 是否为 true；是否装了 `mineru[full]` |

---

## 参考文件

- `references/installation.md` — 从零安装、模型、环境变量、GPU 扩展
- `references/api.md` — HTTP API 字段、示例、错误码
- `references/gpu.md` — 显卡选择、VRAM、速度估算、Windows/Linux 差异
- `scripts/pdf2md_client.py` — 其他 AI 可直接用的 Python 客户端
- `scripts/detect_gpu.py` — 独立 GPU 检测
- `scripts/expand_tables.py` — 把 HTML 合并表展开成逻辑网格（TSV/JSON）
