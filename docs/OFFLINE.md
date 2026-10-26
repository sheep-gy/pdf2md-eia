# 离线使用与离线部署

## 结论

- **转换本身完全离线**：PDF 本地 MinerU，Office 本地解析/LibreOffice，不上传。
- **首次安装依赖需要联网**：Python/MinerU、模型、CUDA、LibreOffice。
- 装好之后可以断网日常使用。
- API 模式只需要局域网，不需要外网。

---

## 在线 / 离线对照

| 动作 | 是否需要外网 |
|---|---|
| 首次运行自检 | 否 |
| 下载 Python/MinerU/模型 | **是** |
| 下载 CUDA 后端 | **是** |
| 下载 LibreOffice | **是** |
| PDF 转换 | 否 |
| DOCX/XLSX 转换 | 否 |
| DOC/XLS 转换 | 否（已装 LibreOffice） |
| HTTP API 调用 | 否（局域网） |
| 上传/下载文件 | 否（API 本机/局域网） |

程序转换时不会把文件发到任何云端服务。

---

## 离线部署步骤

### 推荐：同路径拷贝

1. 在一台有网的 Windows 机器上运行首次安装；
2. 安装目录选成和离线机器**完全相同**的路径，例如都用 `D:\PDF2MD_runtime`；
3. 安装时把需要的模型、LibreOffice、CUDA 都装好；
4. 把下面这些按原路径拷到离线机器：
   - `PDF转MD_桌面版\`（exe + `_internal`）
   - `D:\PDF2MD_runtime\`（`mineru_env` + `mineru_home`）
   - 如果模型装在别的盘，也按原路径一起拷；
5. 离线机器上双击 `PDF转MD.exe`，首次自检应显示“环境已就绪”。

### 为什么强调“同路径”

Python 虚拟环境里的启动脚本记录的是绝对路径。换盘符/换目录后：

- `python.exe` 可能还能启动；
- 但 `mineru-kit.exe` 等 console script 可能指向旧路径，导致 `quality_tier_unavailable` 或找不到模块。

如果必须换路径，最稳妥的是在目标路径重新创建环境并安装依赖；
离线环境下这需要本地 wheel 缓存或离线安装包。

---

## 只装 Office 解析可以更小

- DOCX/XLSX 由 exe 内置的 Python 库解析，不需要 MinerU；
- 但当前 GUI 的启动检查仍要求 MinerU（否则“开始转换”是灰的）；
- 如果你只需要 Office、且完全离线，可以提 issue/需求，增加“仅 Office 模式”。

---

## 离线安装包（尚未提供）

目前仓库只提供源码/文档，不含：

- 224 MB 的 Windows 运行时
- 约 2 GB 的 MinerU 模型
- LibreOffice 安装包
- CUDA/Torch wheel

如果要真正的一键离线部署，可以做一个约 4–6 GB 的离线包：

- uv + Python 3.12 独立发行版
- MinerU 及依赖 wheel
- basic + standard 模型
- LibreOffice 安装包
- 可选的 CUDA/Torch wheel

需要的话可以在 issue 里提出。

---

## 常见问题

**Q：断网后首次启动会不会卡住？**
A：不会。自检只读本地路径；缺依赖时提示需要联网安装。已装好的环境会直接显示“运行环境就绪”。

**Q：API 模式需要外网吗？**
A：不需要。`serve` 只监听本机/局域网；客户端通过内网 IP 访问即可。务必设置 token。

**Q：模型可以放 NAS/共享盘吗？**
A：可以，但路径必须是本机可访问的绝对路径；网络盘断开会导致转换失败。建议放本地磁盘。
