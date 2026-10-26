# GPU 策略与速度

## 1. 先判断“有没有卡”和“后端是否就绪”

```bash
python scripts/detect_gpu.py
```

返回：

```json
{
  "hardware": true,
  "name": "NVIDIA GeForce RTX 4090",
  "vram_gb": 24.0,
  "torch_cuda": true,
  "engine": "lmdeploy",
  "backend_ready": true,
  "device": "cuda",
  "small_backend": "torch",
  "vlm_engine": "lmdeploy"
}
```

- `hardware`：`nvidia-smi` 能看到卡。
- `torch_cuda`：`torch.cuda.is_available()`。
- `engine`：Windows 上是 `lmdeploy`，Linux 上是 `vllm`。
- `backend_ready`：三者都满足，才真的能走 GPU。

**不要把 `hardware=true` 当成 GPU 已生效。** 只看 `backend_ready`。

---

## 2. 有卡但 backend_ready=false 怎么办

装 CUDA 版 MinerU：

```bat
uv pip install "mineru[full]>=4.0,<5" ^
  --python D:\mineru_env\Scripts\python.exe ^
  -i https://mirrors.aliyun.com/pypi/simple
```

- Windows：Torch + LMDeploy。
- Linux / WSL2：Torch + vLLM（更稳）。
- 确认 `nvidia-smi` 正常，驱动支持当前 CUDA。

装完再跑 `detect_gpu.py`，`backend_ready` 变 true 才算成功。

---

## 3. 速度参考

以 218 页原生文字版报告为例（同一份 PDF）：

| 配置 | 全份耗时 | 单页 |
|---|---|---|
| CPU basic + 少量 standard 补漏 | 约 12.8 分钟 | 3.5 秒 |
| GPU basic + 少量补漏 | 约 4–6 分钟 | 1.0–1.8 秒 |
| GPU standard 全页 | 约 4–8 分钟 | 1.1–2.2 秒 |
| GPU advanced 全页 | 约 7–12 分钟 | 2–3.5 秒 |

- VLM（standard/advanced）在 GPU 上比 CPU 快 **10–20 倍**。
- basic 档不是纯 GPU 任务：PDF 解析、文字层取字、线条网格重建仍是 CPU 活，
  所以整体通常只有 **2–3.5 倍**，不是 10 倍。
- 扫描件 OCR 在 GPU 上收益更大，常见 3–6 倍。

全量报告书（1414 份 / 54.5 万页）估算：

| 配置 | 单进程 | 两进程 |
|---|---|---|
| CPU | 约 22 天 | 约 12 天 |
| 24 GB 独显 | 约 7–10 天 | 约 4–5 天（basic 档） |

> VLM 在 24 GB 卡上单 worker 约 13 GB 峰值，两个 VLM worker 容易 OOM；
> basic 小模型显存需求低，可以多开。

---

## 4. 显卡选择

| 显存 | 能做什么 | 建议 |
|---|---|---|
| 8 GB | basic；standard 用 llama.cpp 勉强 | 单 worker，收益有限 |
| 16 GB | basic + standard（llama.cpp） | 单 worker；vLLM 默认可能 OOM |
| **24 GB（3090/4090）** | **推荐** | 1 个 VLM worker；basic 可多开 |
| 48 GB | 多 worker / 高并发 | 批量吞吐 |

模型本身只有约 2 GB，显存主要被 vLLM 的 KV cache 占用（默认 `gpu_memory_utilization=0.5`）。
文档页数不会让显存线性增长；并发才会。

---

## 5. Windows 与 Linux

- **Linux / WSL2**：vLLM 路径成熟，推荐。
- **Windows**：MinerU 4.0 走 Torch + LMDeploy；能用，但稳定性和速度不如 Linux。
- 如果机器可以装 WSL2，优先在 WSL2 里装 `mineru[full]` 并提供 API。
- 中文路径已实测可用；若个别引擎报 Unicode 错误，再把环境/模型换到英文路径。

---

## 6. 什么时候用哪个档

| 场景 | 档位 |
|---|---|
| 普通原生文字版报告 | `basic` |
| 表格特别复杂、列线歪、合并多 | `standard`（GPU）或 `advanced` |
| 扫描件 | `basic`（OCR）；关键表可 `standard` 抽检 |
| 只有 CPU、又要快 | `basic`，靠页级 coverage 只补少数页 |
| 有 GPU、追求质量 | `standard` 全页，或 `basic` + `advanced` 补疑难页 |

注意：GPU 不会自动修好表格字序。表格的 rowspan/colspan 和单元格文字顺序仍由
本项目的线条网格重建负责；`standard` 只是在版面识别上更强。

---

## 7. 能力上报

API 的 `/capabilities` 已经把这些信息整理好：

```json
{
  "gpu": {"hardware": true, "backend_ready": true, "device": "cuda", ...},
  "recommended": {"device": "cuda", "tier": "standard"}
}
```

其他电脑的 AI 应该：

1. 先 GET `/capabilities`；
2. 按 `recommended` 传参；
3. 如果 `backend_ready=false`，不要传 `device=cuda`，先提示用户装 CUDA 后端。

---

## 8. 显式 `device=cuda` 但后端没就绪

转换器会**回退到 CPU 并打一条警告**，不会让任务直接失败：

```
警告：指定了 cuda，但 GPU 后端未就绪，已回退 CPU。
```

所以客户端可以放心先查 `/capabilities`；即使误传 cuda，也不会把整批任务跑挂。
但为了速度和可预期性，仍应先判断 `backend_ready` 再传。
