# 来源映射

[English](SOURCE_MAPPING.md) | [README](../README.zh-CN.md)

新转换默认生成 `source_map.json`，用于测试和核验时快速定位原文，减少翻页与搜索。它是来源索引，不是完整语义模型，也不自动替代审核。

## 查询

```bash
python scripts/pdf2md.py trace --output-dir ../result --line 25
python scripts/pdf2md.py trace --output-dir ../result --object-id OBJECT_ID
```

返回 JSON。每个对象包含 `id`、`kind`、`text`、`start`、`end`、`line_start`、`line_end`、`page`、`status`、`sources`。字符区间采用最终 `manual.md` 中的 Unicode code point 半开区间，不是 UTF-8 字节或 UTF-16 单元。行号与 PDF 物理页码从 1 开始。

每个来源包含原文、关联依据和 `bbox`：坐标为未旋转页面左上角起算的 PDF points。`pages` 记录尺寸和原始旋转角度。多个来源表示候选，不能默认采用第一项。

| 状态 | 含义 |
| --- | --- |
| `MATCHED_SOURCE_TEXT` | 唯一输出出现与独立读取的 PDF 文本块匹配 |
| `ASSOCIATED_NATIVE_LAYOUT` | 依据原生布局或有记录的来源操作定位，内容仍可能出错 |
| `AMBIGUOUS` | 存在多个候选、重复输出或位置证据冲突 |
| `UNMAPPED` | 未获得可靠来源区域 |

对象范围为标题、段落、列表项、整张表格、以公式形式表达的内容及图片。以普通文本输出的公式仍归段落；本索引不将图片转成可编辑符号。不提供单元格级、符号级映射，也不认证全文完整性。

## 按需截图

```bash
python scripts/pdf2md.py trace --output-dir ../result --line 25 \
  --input ../manual.pdf --render-dir ../evidence-line25
```

原 PDF 哈希必须一致。截图目录必须是新目录，位于封存结果和工程目录之外，且不能包含原 PDF。截图按未旋转来源坐标导出，哈希与来源记录保存在 `evidence.json`。歧义对象导出全部可用候选，不自动选中；未定位对象不生成裁图。不覆盖已有证据目录。

转换默认不复制 PDF、不生成截图。查询前核验封存和映射绑定；`verify` 检查索引覆盖、位置、ID、坐标和图片哈希，不判断技术内容是否正确。旧结果没有映射时仍可校验，但不能直接 `trace`，不会被补写或重新封存。
