# -*- coding: utf-8 -*-
"""检测独显与 MinerU GPU 后端是否就绪，供 CLI / API / skill 共用。"""
import os
import shutil
import subprocess
import sys
import time

_CACHE = {"t": 0, "v": None}
MINERU_PY = os.environ.get("MINERU_PY", r"D:/mineru_env/Scripts/python.exe")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW)
        if p.returncode != 0:
            return None
        line = (p.stdout or "").strip().splitlines()[0]
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 3:
            return None
        return {"name": parts[0], "vram_mb": int(float(parts[1])),
                "driver": parts[2]}
    except Exception:
        return None


def _external_py(code, timeout=30):
    """用外部 MinerU 环境执行一小段代码（打包成 exe 后自身看不到 torch）。"""
    if not os.path.exists(MINERU_PY):
        return None
    try:
        p = subprocess.run([MINERU_PY, "-c", code], capture_output=True,
                           text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        if p.returncode != 0:
            return None
        return (p.stdout or "").strip()
    except Exception:
        return None


def _torch_cuda():
    try:
        import torch  # noqa
        if torch.cuda.is_available():
            return True
    except Exception:
        pass
    # exe 里没有 torch，去外部 MinerU 环境问
    out = _external_py("import torch;print(1 if torch.cuda.is_available() else 0)")
    return out == "1"


def _engine_ready():
    """当前平台可用的 GPU VLM 引擎。"""
    if sys.platform.startswith("win"):
        try:
            import lmdeploy  # noqa
            return "lmdeploy"
        except Exception:
            out = _external_py("import lmdeploy;print('lmdeploy')")
            return out or None
    try:
        import vllm  # noqa
        return "vllm"
    except Exception:
        pass
    try:
        import lmdeploy  # noqa
        return "lmdeploy"
    except Exception:
        out = _external_py("import vllm;print('vllm')")
        if out:
            return out
        out = _external_py("import lmdeploy;print('lmdeploy')")
        return out or None


def detect():
    if _CACHE["v"] is not None and time.time() - _CACHE["t"] < 30:
        return _CACHE["v"]
    hw = _nvidia_smi()
    torch_cuda = _torch_cuda()
    engine = _engine_ready()
    ready = bool(hw and torch_cuda and engine)
    out = {
        "hardware": bool(hw),
        "name": (hw or {}).get("name"),
        "vram_gb": round((hw or {}).get("vram_mb", 0) / 1024, 1) if hw else 0,
        "driver": (hw or {}).get("driver"),
        "torch_cuda": torch_cuda,
        "engine": engine,
        "backend_ready": ready,
        "device": "cuda" if ready else "cpu",
        "small_backend": "torch" if ready else "onnx",
        "vlm_engine": engine if ready else "llama-cpp",
        "note": (
            "有独显且 CUDA 后端就绪，优先走 GPU"
            if ready else
            ("检测到独显，但未装 mineru[full] 的 Torch/LMDeploy/vLLM 后端，当前仍走 CPU"
             if hw else "未检测到 NVIDIA 独显，走 CPU")
        ),
    }
    _CACHE["t"], _CACHE["v"] = time.time(), out
    return out


def apply_env(env, device="auto"):
    """把设备选择写进 MinerU 子进程环境。device: auto|cpu|cuda。"""
    if device == "cpu":
        # 明确要 CPU 时不再探测 GPU，避免每份文件多等 5-10 秒
        env["MINERU_MODEL_SMALL_BACKEND"] = "onnx"
        env["MINERU_MODEL_VLM_ENGINE"] = "llama-cpp"
        return env, False
    g = detect()
    want_gpu = device in ("auto", "cuda")
    use_gpu = want_gpu and g["backend_ready"]
    if device == "cuda" and not g["backend_ready"] and sys.stderr is not None:
        print("警告：指定了 cuda，但 GPU 后端未就绪，已回退 CPU。"
              f"（{g['note']}）", file=sys.stderr)
    env["MINERU_MODEL_SMALL_BACKEND"] = "torch" if use_gpu else "onnx"
    env["MINERU_MODEL_VLM_ENGINE"] = (g["vlm_engine"] if use_gpu else "llama-cpp")
    return env, use_gpu
