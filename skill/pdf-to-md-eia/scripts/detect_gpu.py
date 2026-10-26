# -*- coding: utf-8 -*-
"""独立检测 NVIDIA 独显与 MinerU GPU 后端，输出 JSON。

用法：python detect_gpu.py
"""
import json
import os
import shutil
import subprocess
import sys

MINERU_PY = os.environ.get("MINERU_PY", r"D:/mineru_env/Scripts/python.exe")


def nvidia():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        if p.returncode != 0:
            return None
        parts = [x.strip() for x in (p.stdout or "").strip().splitlines()[0].split(",")]
        if len(parts) < 3:
            return None
        return {"name": parts[0], "vram_mb": int(float(parts[1])), "driver": parts[2]}
    except Exception:
        return None


def external(code, timeout=30):
    if not os.path.exists(MINERU_PY):
        return None
    try:
        p = subprocess.run([MINERU_PY, "-c", code], capture_output=True,
                           text=True, timeout=timeout)
        return (p.stdout or "").strip() if p.returncode == 0 else None
    except Exception:
        return None


def torch_cuda():
    try:
        import torch
        if torch.cuda.is_available():
            return True
    except Exception:
        pass
    return external("import torch;print(1 if torch.cuda.is_available() else 0)") == "1"


def engine():
    if sys.platform.startswith("win"):
        try:
            import lmdeploy  # noqa
            return "lmdeploy"
        except Exception:
            out = external("import lmdeploy;print('lmdeploy')")
            return out or None
    for name in ("vllm", "lmdeploy"):
        try:
            __import__(name)
            return name
        except Exception:
            pass
    for name in ("vllm", "lmdeploy"):
        out = external(f"import {name};print('{name}')")
        if out:
            return out
    return None


def main():
    hw = nvidia()
    tc = torch_cuda()
    eng = engine()
    ready = bool(hw and tc and eng)
    out = {
        "hardware": bool(hw),
        "name": (hw or {}).get("name"),
        "vram_gb": round((hw or {}).get("vram_mb", 0) / 1024, 1) if hw else 0,
        "driver": (hw or {}).get("driver"),
        "torch_cuda": tc,
        "engine": eng,
        "backend_ready": ready,
        "device": "cuda" if ready else "cpu",
        "small_backend": "torch" if ready else "onnx",
        "vlm_engine": eng if ready else "llama-cpp",
        "note": (
            "有独显且 CUDA 后端就绪，优先走 GPU"
            if ready else
            ("检测到独显，但未装 mineru[full] 的 Torch/LMDeploy/vLLM 后端，当前仍走 CPU"
             if hw else "未检测到 NVIDIA 独显，走 CPU")
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
