# tools/

构建 Windows 桌面版时，把 `uv.exe` 放在这里：

```
tools/uv.exe
```

`build_gui.py` 会把它打进 `dist/EIA report to md/_internal/tools/uv.exe`，
用于首次运行时在没有 uv 的机器上离线创建 Python 环境。

`uv.exe` 不随源码仓库提交（约 67 MB），可从
<https://github.com/astral-sh/uv/releases> 下载
`uv-x86_64-pc-windows-msvc.zip` 后解压得到。
