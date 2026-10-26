# 安装与构建

## 1. 机器要求

| 项目 | 建议 |
|---|---|
| 系统 | Windows 10/11 x64（桌面版）；CLI/API 源码可跨平台 |
| Python | 3.12（MinerU 4.x 推荐） |
| 内存 | 单进程 5–6 GB；23 GB 建议 2 并发，16 GB 建议 1–2 |
| 显卡 | 可选。NVIDIA、显存 ≥ 8 GB 时可装 CUDA 后端 |
| 磁盘 | Python 环境约 0.8 GB；模型约 2 GB；Office 支持 LibreOffice 约 350 MB |
| 网络 | 首次安装依赖需要；之后可离线 |

---

## 2. 从源码运行

```bat
git clone https://github.com/sheep-gy/pdf2md-eia.git
cd pdf2md-eia

uv venv --python 3.12 .venv
uv pip install -r requirements.txt --python .venv\Scripts\python.exe

.venv\Scripts\python.exe cli.py
```

`cli.py` 不带参数会打开 GUI。首次启动会自检；缺少 MinerU/模型时，
按弹窗提示选择安装目录并点“开始安装并配置”，程序会自动安装。

> 也可以不用 uv，用 `python -m venv .venv` + `pip install -r requirements.txt`。

---

## 3. MinerU 与模型

桌面版/源码 GUI 的首次安装会自动完成；手动安装如下：

```bat
uv venv --python 3.12 D:\mineru_env
uv pip install "mineru>=4.0,<5" pymupdf pdfplumber ^
  --python D:\mineru_env\Scripts\python.exe ^
  -i https://mirrors.aliyun.com/pypi/simple

set MINERU_HOME=D:\mineru_home
set MINERU_MODEL_SOURCE=modelscope

D:\mineru_env\Scripts\mineru-kit.exe models download --tier basic ^
  --small-backend onnx --source modelscope

D:\mineru_env\Scripts\mineru-kit.exe models download --tier standard ^
  --small-backend onnx --vlm-engine llama-cpp --source modelscope
```

校验：

```bat
D:\mineru_env\Scripts\mineru-kit.exe models verify --tier basic --small-backend onnx
```

### GPU（可选）

```bat
uv pip install "mineru[full]>=4.0,<5" ^
  --python D:\mineru_env\Scripts\python.exe ^
  -i https://mirrors.aliyun.com/pypi/simple
```

校验：

```bat
D:\mineru_env\Scripts\python.exe -c "import torch,lmdeploy;print(torch.cuda.is_available())"
```

`True` 才说明 CUDA 后端可用；否则转换会自动回退 CPU。

### LibreOffice（旧版 DOC/XLS，可选）

```bat
winget install --id TheDocumentFoundation.LibreOffice -e
```

DOCX/XLSX 不需要 LibreOffice。

---

## 4. 构建 Windows 桌面版

### 4.1 准备

- Python 3.12 + PyInstaller 6.x
- `tools\uv.exe`（仓库不提交，约 67 MB）
  - 从 <https://github.com/astral-sh/uv/releases> 下载
    `uv-x86_64-pc-windows-msvc.zip`，解压出 `uv.exe`

### 4.2 构建

```bat
python -m pip install pyinstaller
python build_gui.py
```

产物：

```
dist\PDF转MD\
├─ PDF转MD.exe
├─ _internal\
├─ 使用说明.txt
├─ AI调用说明.md
├─ API接口.md
├─ AI调用示例.py
└─ skill\pdf-to-md-eia\
```

`PDF转MD.exe` 必须和 `_internal\` 同目录。

### 4.3 打包说明

- onedir，`console=False`（双击无黑框；命令行模式自动附加控制台）；
- 包含 Tkinter、pymupdf、pdfplumber、mammoth、python-docx、openpyxl、bs4；
- 不包含 MinerU、模型、LibreOffice；这些由首次运行安装；
- `tools\uv.exe` 会进 `_internal\tools\`，用于离线创建 Python 环境。

---

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| `No module named mineru` / `quality_tier_unavailable` | MinerU 环境或模型没装好；跑一次 GUI 首次安装，或按第 3 节手动装 |
| GPU 未生效 | 先跑 `PDF转MD.exe capabilities`，看 `backend_ready`；false 时不要传 `cuda` |
| `No mapping for the Unicode character exists` | 中文路径已支持；极少数引擎仍报错时，把环境/模型换到英文路径 |
| LibreOffice 安装失败 | 检查 winget、UAC、网络；或手动安装 LibreOffice |
| 杀毒软件报警 | PyInstaller 程序常被误报，添加信任 |
| 模型下载慢 | 默认 ModelScope；也可设 `MINERU_MODEL_SOURCE=huggingface` 或手动下载 |
| 内存不足 | 降低并发；每进程约 5–6 GB |
| 首次安装失败 | 弹窗看失败步骤；换镜像重试；已完成的步骤不会重下 |

---

## 6. 目录与配置

| 内容 | 位置 |
|---|---|
| 配置 | `%LOCALAPPDATA%\PDF2MD\config.json` |
| 日志 | `%LOCALAPPDATA%\PDF2MD\logs\` |
| 任务临时目录 | `%LOCALAPPDATA%\PDF2MD\jobs\` |
| MinerU 环境 | 默认 `D:\mineru_env`，可在安装时改 |
| 模型 | 默认 `D:\mineru_home`，可在安装时改 |

删除 `config.json` 可重新触发首次自检/安装向导。
