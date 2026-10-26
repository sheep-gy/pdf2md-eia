# 支持的文件格式

## 总表

| 格式 | 后缀 | 引擎 | 输出 | 需要 MinerU | 需要 LibreOffice |
|---|---|---|---|---|---|
| PDF | `.pdf` | MinerU + 线条网格重建 | `.md` + `_qa.json` | 是 | 否 |
| Word（现代） | `.docx` | mammoth / python-docx | `.md` + `_qa.json` | 否 | 否 |
| Excel（现代） | `.xlsx` | openpyxl | `.md` + `_qa.json` | 否 | 否 |
| Word（旧版） | `.doc` | LibreOffice → docx → mammoth | `.md` + `_qa.json` | 否 | **是** |
| Excel（旧版） | `.xls` | LibreOffice → xlsx → openpyxl | `.md` + `_qa.json` | 否 | **是** |
| PowerPoint | `.pptx` | — | — | — | — |

PPTX 暂不支持；如有需要可在后续版本增加。

---

## PDF

### 适合

- 中文环评报告书/报告表、竣工验收、排污许可、导则/标准
- 带跨行跨列、整页外框、跨页续表、上下标、复选框的复杂表
- 扫描件/文字层损坏的 PDF
- 需要页码锚点、页级质量指标、给 AI 分章节读取的场景

### 输出

- `文件名.md`：正文 + `<!-- p123 -->` 页码锚点 + HTML 表格
- `文件名_qa.json`：质量报告

### 约定

- 默认 `basic` 档；漏字页自动用 `standard` 补跑；
- 图片只保留图题，不嵌二进制；
- 表格保留 `rowspan/colspan`；严格抽取时用 `expand_tables.py` 展开；
- 少于 20 页的 PDF 默认跳过（GUI 可取消勾选；CLI 默认不跳过）。

### QA 字段

```json
{
  "pages": 218,
  "parsed_pages": 218,
  "text_pages": 210,
  "chars": 266700,
  "tables_mid": 146,
  "tables_md": 150,
  "min_page_recall": 0.9665,
  "avg_page_recall": 0.9967,
  "pages_below_0.9": 0,
  "suspicious_cells": 2,
  "pua_left": 0,
  "mineru_sec": 559.9,
  "repaired_pages": [31, 32, 36, 100, 151, 161],
  "native_pages": [101],
  "recovered_pages": [],
  "total_sec": 765.8
}
```

---

## DOCX

### 引擎

`mammoth` 把 DOCX 转 HTML，再用 BeautifulSoup 转 Markdown。

- 标题：`Title` / `Subtitle` / `Heading 1..6`
- 段落、列表、引用、代码块
- 表格：保留 HTML `<table>`，合并单元格用 `rowspan/colspan`
- mammoth 失败时回退 `python-docx` 按 body 顺序遍历

### 输出

```markdown
<!-- source: 某某报告.docx -->

# 标题

正文段落……

<table><tr><td>项目</td><td>数值</td></tr>...</table>
```

### 约定

- 不走 MinerU；通常 1 秒内完成；
- 图片不做 OCR，只保留 alt 文本；
- 不生成页码锚点；
- QA 中 `pages=0`，`tables_md`、`warnings` 等字段有效。

---

## XLSX

### 引擎

`openpyxl.load_workbook(data_only=True)`，按工作表输出。

- 每个 sheet 一个 `## 工作表名`
- 合并区域填入左上角值
- 首行作为表头；空表头补 `列N`
- 值转义 `|` 和换行
- 公式读取缓存值（需在 Excel 中保存过）

### 输出

```markdown
## 数据

| 项目 | 数值 | 备注 |
| --- | --- | --- |
| COD | 12.5 | mg/L |
| 合并单元格示例 | 合并单元格示例 | 合并单元格示例 |
```

### 约定

- 不生成页码锚点；
- QA 中 `pages=0`，`sheets` 为工作表数，`tables_md` 为表数；
- 超大工作表会生成很大的 Markdown；建议按 sheet 拆分或先筛选。

---

## DOC / XLS（旧版）

### 引擎

1. 找 `soffice`（PATH / `C:\Program Files\LibreOffice\program\soffice.exe`）；
2. `soffice --headless --convert-to docx|xlsx`；
3. 复用 DOCX/XLSX 流程。

### 安装 LibreOffice

- GUI 第一次添加 `.doc` / `.xls` 时会提示自动安装（winget，约 350 MB，可能弹 UAC）；
- 也可以从菜单 **环境 → 安装旧版 DOC/XLS 支持** 触发；
- 或手动：`winget install --id TheDocumentFoundation.LibreOffice -e`。

### 约定

- 质量取决于 LibreOffice 的转换结果；
- 没有 LibreOffice 时会明确报错；
- 不生成页码锚点。

---

## 跳过规则

| 规则 | PDF | Office |
|---|---|---|
| 少于 20 页跳过 | 是（GUI 默认开） | 否 |
| 已存在 `.md` 且未勾选覆盖 | 是 | 是 |
| 非支持后缀 | 是 | 是 |

---

## 批量与目录

- GUI「添加文件夹」会递归扫描 `.pdf/.docx/.doc/.xlsx/.xls`；
- `batch.py` 同样支持这些后缀，`--pattern 报告书` 只按路径过滤；
- `batch.py --pattern ""` 可处理整个目录；
- 输出默认保留原目录层级。

```bat
PDF转MD.exe batch "D:\报告" -o "D:\md" --pattern "" --workers 2
```
