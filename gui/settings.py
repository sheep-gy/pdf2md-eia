# -*- coding: utf-8 -*-
"""配置、数据目录与持久化。"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """exe 所在目录（源码运行时是项目根目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """PyInstaller 解包目录；源码运行时等于项目根目录。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()


def data_dir() -> Path:
    if is_frozen() and (app_dir() / "portable.ini").exists():
        return app_dir() / "data"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "PDF2MD"


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def jobs_dir() -> Path:
    p = data_dir() / "jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bin_dir() -> Path:
    p = data_dir() / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_env_dir() -> Path:
    d = Path(r"D:\mineru_env")
    if d.exists():
        return d
    return data_dir() / "runtime" / "mineru_env"


def default_home_dir() -> Path:
    d = Path(r"D:\mineru_home")
    if d.exists():
        return d
    return data_dir() / "runtime" / "mineru_home"


@dataclass
class Config:
    schema: int = 1
    app_version: str = "2.0.0"
    env_dir: str = ""
    mineru_home: str = ""
    mineru_py: str = ""
    mineru_bin: str = ""
    uv_path: str = ""
    tier: str = "basic"
    device: str = "cpu"
    workers: int = 1
    out_mode: str = "same"          # same | custom
    out_dir: str = ""
    preserve_tree: bool = True
    skip_lt_pages: int = 20
    overwrite: bool = False
    keep_json: bool = False
    install_standard: bool = True
    install_legacy: bool = False
    onboarding_done: bool = False
    env_ok: bool = False
    last_check: str = ""
    window: dict = field(default_factory=lambda: {"w": 1180, "h": 720, "x": 80, "y": 60})
    gpu_cache: dict = field(default_factory=dict)

    def __post_init__(self):
        env = Path(self.env_dir) if self.env_dir else default_env_dir()
        home = Path(self.mineru_home) if self.mineru_home else default_home_dir()
        self.env_dir = str(env)
        self.mineru_home = str(home)
        if not self.mineru_py:
            self.mineru_py = str(env / "Scripts" / "python.exe")
        if not self.mineru_bin:
            self.mineru_bin = str(env / "Scripts" / "mineru-kit.exe")

    @property
    def env_py(self) -> Path:
        return Path(self.mineru_py)

    @property
    def kit(self) -> Path:
        return Path(self.mineru_bin)

    @property
    def home(self) -> Path:
        return Path(self.mineru_home)


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> Config:
    defaults = Config()
    p = config_path()
    if not p.exists():
        return defaults
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        try:
            p.replace(p.with_suffix(".json.bad"))
        except Exception:
            pass
        return defaults
    merged = asdict(defaults)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in merged})
    try:
        return Config(**merged)
    except Exception:
        return defaults


def save_config(cfg: Config) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
