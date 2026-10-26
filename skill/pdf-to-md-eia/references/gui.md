# 桌面版 GUI 与首次自动安装

本文件说明 `PDF转MD_桌面版` 的图形界面、首次自检和自动安装流程，
方便其他 AI 指导用户完成部署。

## 0. 支持格式

- PDF：MinerU 高质量链路
- DOCX / XLSX：原生解析，不需要 MinerU，速度快
- DOC / XLS：需要 LibreOffice；第一次添加时 GUI 会提示自动安装（winget，约 350 MB）

## 1. 启动

双击 `PDF转MD.exe`。exe 是 GUI 子系统，不会出现黑色控制台。

首次启动会做一次自检，然后弹窗：

- CPU 物理核 / 线程
- 内存总量 / 当前可用
- 是否有 NVIDIA 独显、显存、驱动
- 安装盘可用空间
- 推荐并发数（按可用内存 ÷ 6 GB 和物理核数）
- 是否建议使用独显

## 2. 环境已就绪时

如果 MinerU Python 环境、mineru-kit、基础模型都已存在，弹窗只显示：

- 依赖状态（MinerU 版本、基础模型、增强模型、显卡）
- 推荐并发数
- “确认并开始使用”按钮

**不会询问安装位置，也不会提示下载任何东西。**

## 3. 缺少依赖时

弹窗显示：

- 缺少哪些组件、各有多大
- 一个“安装目录”（支持中文路径）
- 软件自动在该目录下创建：
  - `mineru_env`（Python 3.12 + MinerU + pymupdf/pdfplumber）
  - `mineru_home`（基础模型 + 增强模型）
- 如果检测到 NVIDIA 显卡，会默认勾选“使用 NVIDIA 独显”，
  并自动安装 `mineru[full]` 的 CUDA 后端（约 3.5 GB）

点“开始安装并配置”后全自动执行，不需要用户再敲任何命令：

1. 找 uv；没有就自动下载
2. 创建 Python 3.12 环境
3. 安装 MinerU / pymupdf / pdfplumber（阿里云 → 清华 → 官方 PyPI）
4. 下载基础模型（ModelScope → HuggingFace）
5. 下载增强补漏模型
6. 安装并校验 CUDA 后端（如果勾选独显）
7. 全部校验通过后弹窗“所有依赖已下载、安装并校验完成”

## 4. 进度显示

安装时界面显示：

- 总进度：第 N/6 步
- 当前步骤：uv / Python 环境 / MinerU 依赖 / 基础模型 / 增强模型 / 校验
- 下载进度条：百分比、已下载 MB（能解析时）
- 下载完成后显示“正在安装：…”
- 可随时“取消安装”；已完成的步骤保留，下次继续

## 5. 安装完成后

- 配置保存在 `%LOCALAPPDATA%\PDF2MD\config.json`
- 日志在 `%LOCALAPPDATA%\PDF2MD\logs\`
- 主界面可以添加 PDF/文件夹、选输出目录、开始/停止、看 QA
- 命令行/API 仍然可用（见 `AI调用说明.md`）

## 5.1 离线使用

- 转换本身完全离线（PDF 用本地 MinerU，Office 用本地库/LibreOffice）。
- 首次安装依赖需要联网（Python/MinerU/模型/LibreOffice/CUDA）。
- 离线部署：在联网机器上把安装目录设成与离线机器相同的路径，
  装完后把 `PDF转MD_桌面版\` 和运行时目录按原路径拷贝过去。
  venv 记录绝对路径，换路径可能失效；不要只拷 exe。

## 6. 给其他 AI 的建议

如果你的用户要在新机器上部署：

1. 让用户双击 exe，按弹窗完成自检和自动安装（只需点一次“开始安装并配置”）。
2. 安装完成后，再启动 `PDF转MD.exe serve --host 0.0.0.0 --port 8765 --token 密钥`。
3. 调用前先 `GET /capabilities`，按 `recommended` 传 `device` / `tier`。
4. 不要假设 CUDA 已就绪；以 `backend_ready` 为准。
5. 中文路径已支持；若个别引擎仍报 Unicode 错误，再把环境/模型改到英文路径。
