# 安装与环境

## 1. 机器要求

| 项目 | 建议 |
|---|---|
| 系统 | Windows 10/11 x64；GPU 用户优先 Linux/WSL2 |
| CPU | 8 核 16 线程以上 |
| 内存 | 单进程约 2.5–3 GB；23 GB 内存建议 2 个并发 |
| 显卡 | 可选。NVIDIA，24 GB 显存最稳（RTX 3090/4090） |
| 磁盘 | 环境 1.3 GB + 模型 2.0 GB；输出 md 约为原 PDF 的 1%–3% |

不需要独显也能跑；GPU 只影响速度，不改变输出格式。

---

## 2. 当前这台机器（已装好）

| 项目 | 路径 |
|---|---|
| Python 3.12 + MinerU 4.0.12 | `D:\mineru_env` |
| PyMuPDF 2.0 / pdfplumber 0.11.10 / Flask | `D:\mineru_env` |
| basic ONNX 模型（819 MB） | `D:\mineru_home\models\MinerU-4_models_onnx` |
| standard VLM 模型（1.2 GB） | `D:\mineru_home\models\MinerU2.5-Pro-2605-1.2B-GGUF` |
| 转换程序 exe | `EIA-report-to-md\EIA-report-to-md.exe` |

---

## 3. 从零安装（CPU）

```bat
winget install astral-sh.uv

uv venv --python 3.12 D:\mineru_env
uv pip install "mineru>=4.0,<5" pymupdf pdfplumber flask ^
  --python D:\mineru_env\Scripts\python.exe ^
  -i https://mirrors.aliyun.com/pypi/simple
```

下载模型：

```bat
set MINERU_HOME=D:\mineru_home
set MINERU_MODEL_SOURCE=modelscope

D:\mineru_env\Scripts\mineru-kit.exe models download --tier basic ^
  --small-backend onnx --source modelscope

D:\mineru_env\Scripts\mineru-kit.exe models download --tier standard ^
  --small-backend onnx --vlm-engine llama-cpp --source modelscope
```

自检：

```bat
set MINERU_HOME=D:\mineru_home
D:\mineru_env\Scripts\mineru-kit.exe models verify --tier basic --small-backend onnx
D:\mineru_env\Scripts\mineru-kit.exe models verify --tier standard --small-backend onnx --vlm-engine llama-cpp
D:\mineru_env\Scripts\mineru-kit.exe version
```

---

## 4. GPU（可选，推荐有卡就装）

### 4.1 安装 CUDA 版 MinerU

```bat
uv pip install "mineru[full]>=4.0,<5" ^
  --python D:\mineru_env\Scripts\python.exe ^
  -i https://mirrors.aliyun.com/pypi/simple
```

- **Windows**：MinerU 4.0 走 Torch + LMDeploy。
- **Linux / WSL2**：走 Torch + vLLM，通常更稳、更快。
- 安装前确认 NVIDIA 驱动和 CUDA 可用：`nvidia-smi`。

### 4.2 验证 GPU 后端

```bash
python scripts/detect_gpu.py
```

关键字段：

```json
{
  "hardware": true,
  "name": "NVIDIA GeForce RTX 4090",
  "vram_gb": 24.0,
  "torch_cuda": true,
  "engine": "lmdeploy",
  "backend_ready": true,
  "device": "cuda"
}
```

只有 `backend_ready=true` 才是真的能走 GPU。

### 4.3 显存

- standard 档 1.2B VLM + vLLM 默认配置约 **13 GB** 峰值 → 24 GB 最稳。
- 16 GB 可能 OOM；可降低 `gpu_memory_utilization` 或用 llama.cpp。
- basic 档小模型 + 表格模型约 4 GB 起。

---

## 5. 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `MINERU_HOME` | `D:/mineru_home` | 模型根目录 |
| `MINERU_BIN` | `D:/mineru_env/Scripts/mineru-kit.exe` | MinerU 可执行文件 |
| `MINERU_PY` | `D:/mineru_env/Scripts/python.exe` | Python 解释器 |
| `MINERU_MODEL_SOURCE` | `local` | `local` 离线；下载时用 `modelscope` |
| `PDF2MD_URL` | `http://127.0.0.1:8765` | 客户端默认 API 地址 |
| `PDF2MD_TOKEN` | 空 | API 鉴权 |
| `PDF2MD_OUT` | `D:/pdf2md_out` | API 默认输出目录 |

---

## 6. 中文路径问题

MinerU 的 llama.cpp 引擎在中文路径下会报：

```
No mapping for the Unicode character exists in the target multi-byte code page.
```

处理：中文路径已实测可用；若个别引擎仍报错，再把 Python 环境和模型换到英文路径（如 `D:\mineru_env`、`D:\mineru_home`）。
PDF 输入/输出路径可以是中文。

---

## 7. 常见安装问题

| 现象 | 处理 |
|---|---|
| `quality_tier_unavailable` | `models verify`；确认 `MINERU_HOME`；不要用 `local` 跑没下载的档位 |
| 模型下载慢 | `--source modelscope`；不要用 `auto` 在 HuggingFace 不通时 |
| `ImportError: libGL.so.1`（WSL） | `sudo apt install libgl1-mesa-glx` |
| GPU 不被识别 | 驱动、CUDA、`mineru[full]` 三者缺一不可 |
| exe 被杀软误报 | PyInstaller 单文件/目录常见；加信任或用源码运行 |
