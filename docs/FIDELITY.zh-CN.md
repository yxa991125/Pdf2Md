# 技术检查与恢复

[English](FIDELITY.md) | [返回 README](../README.zh-CN.md)

普通 `convert` 和 `batch` 会在原生转换后执行 F1。默认 `--fidelity repair`；
`check` 只检查不修改正文，`off` 保留原转换路径。无需创建文档组织工作区或部署额外模型。

## 已有转换结果

```bash
python3 scripts/pdf2md.py fidelity --input /path/manual.pdf \
  --result /path/R0 --mode repair --output-dir /path/R1
python3 scripts/pdf2md.py verify --output-dir /path/R1
python3 scripts/pdf2md.py trace --output-dir /path/R1 --line 25 \
  --input /path/manual.pdf --render-dir /path/new-evidence
python3 scripts/pdf2md.py fidelity --result /path/R1 \
  --mode rollback --output-dir /path/restored-R0
```

原 PDF 的哈希必须与转换记录一致。发布到新目录，不修改 R0。
使用 `--resume` 时保持输入、源码、模式和运行环境一致。回滚恢复原转换的全部封存文件，
不只是把正文替换回去。

## 检查范围

- 数量：有符号数值、范围、科学计数法和原文字面单位；在唯一源行或完整源段落与输出对象内配对。
- 公式：支持的赋值、操作数、四则运算与优先级、简单单层几何分式和字面上下标。
  源与输出均明确出现时，核对公式编号及 `When MODE=0: X=A-B`、
  `X=A-B; when MODE=0` 这类同一行条件。
- 来源侧发现：从原文公式出发检查输出缺失。图像表示、暂不支持的写法和歧义对象保留未决记录。
- 自动修复：唯一原生线性公式中已确认的减号缺失与下划线损坏。
  独立复核器重新读取 PDF 字符与几何信息，核对整条替换公式，不复用候选生成器的公式解析器。
  含分式或条件、编号的公式暂不自动修复。

特殊字体编码、旋转公式、不支持的嵌套脚本、隐式代数变换和复杂表头归属保留未决或未检查状态。
行首带空格的列表横线不直接认作负号。对象定位、已检查属性一致和整个对象正确分别记录。

## 结果文件

`fidelity_assessment.json` 给出来源及输出位置、具体差异、已检查属性，以及
`SOURCE_MATCH`、`SOURCE_MISMATCH`、`ALIGNMENT_AMBIGUOUS`、
`SOURCE_INSUFFICIENT`、`NOT_CHECKED` 状态。
质量报告单列 checked、matched、mismatched、repaired、rejected、unresolved、unchecked。
这些是检查记录数，不是全文准确率。

`fidelity_candidates.json`、`fidelity_repair_log.json` 保存候选和独立复核决定，
`fidelity.diff` 保存精确差分。来源映射中的位置和哈希指向最终正文。
`trace` 同时返回查询对象的技术检查、修复记录和来源证据。

被替换的原始元数据和原 manifest 保存在 `fidelity/parents/`；
未变的资产和原生运行证据保留在结果中，回滚无需重复存储全部图片。
标准转换还会在相邻的 `.<结果名>.fidelity-convert/` 事务目录保留完整原始版本和发布日志。

F1 失败时返回非零退出码，并保留 `fidelity_pending=true` 的完整原始结果。
处理原因后续跑原命令。已完成结果会经过校验后复用，不重复修复。
执行 `verify` 或回滚时请保留完整结果目录。
