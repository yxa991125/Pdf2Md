# 文档组织与局部修订

[English](ORGANIZATION.md) | [README](../README.zh-CN.md)

D1 在已有完整转换结果上工作，不重新解析 PDF。原来的 `convert` 仍生成单文件结果；`organize` 将它导入独立工作区，提供稳定对象、多文件阅读、范围检索和局部修订。模块版本 `d1.2` 与转换产品版本分别记录。

使用已准备好的协调器 Python，无需 GPU、模型或网络。Windows 将下面的 `python3 scripts/pdf2md.py` 换成 `.\scripts\pdf2md.cmd`，支持盘符路径。

## 导入与阅读

```bash
python3 scripts/pdf2md.py organize --from-result /data/manual_pdf2md \
  --workspace /data/organized --input /data/manual.pdf
python3 scripts/pdf2md.py verify --workspace /data/organized
python3 scripts/pdf2md.py scopes --workspace /data/organized
```

`--input` 可省略。提供时校验原 PDF 的 SHA256；没有 PDF 也可组织、检索及读取已有来源元数据。旧封存结果没有 `source_map.json` 时保留正文和页码标记，来源区域明确未映射，不补写旧结果。

同一工作区重复导入相同输入复用身份和结果。不同输入或 `--soft-chars` 配置需新工作区。默认软大小 12000 字符，只在完整对象边界考虑分文件；大表格和公式可超过该值。

```text
organized/
  WORKSPACE.json                 工作区及输入身份绑定
  HEAD.json                      当前已发布版本
  imports/                       原输入清单与首次分配的身份
  revisions/rev-000001/
    README.md                    生成的导航
    content/scope-....md          规范正文，含稳定 Unit 锚点
    content/assets/              独立复制的原图片，字节不变
    index/                       Unit、Scope、关系及检索索引
    source_map.json              本版本的来源关联
    revision.json                操作、重组哈希与生成元数据
    revision_manifest.json       完整文件封存清单
  proposals/                     未直接修改正文的候选
  history/                       保留的失败记录
```

图片保持相对于 `content/` 的原路径，因此无需改写原始图片 URL。正文可逆重组为输入的完整 UTF-8 内容，空白、注释、页码标记和未知片段也有归属。生成的导航、锚点和必要的链接重定位分别记录。

Unit 是稳定的信息单元；Scope 是成员范围；Markdown 是阅读文件；检索片段是查询结果。Unit ID 首次导入时分配并持久化，不依赖内容哈希、行号或字符偏移。

Scope 区分显式结构、带推断属性的结构和中性的虚拟范围。没有目录也可使用。重复标题不合并；层级跳跃、超大对象和不确定来源保留状态。`next_in_output` 只表示转换输出顺序。

## 范围检索

从 `scopes` 输出选择实际 Scope ID：

```bash
python3 scripts/pdf2md.py search --workspace /data/organized \
  --scope scope-ID --query DATA_STATUS --match exact
python3 scripts/pdf2md.py search --workspace /data/organized \
  --pages 20:30 --kind table --query ADCRANGE --match exact
python3 scripts/pdf2md.py search --workspace /data/organized --unit-id u-ID
```

- 先确定允许对象集合，再匹配。Scope、页段、类型和 Unit 过滤取交集。
- `exact` 保留大小写和标识边界，区分 `DATA_STATUS`、`DATA STATUS`、`DATA-STATUS`；仅解码可逆 Markdown 标点转义，返回原始命中区间。
- `substring` 为区分大小写的子串；`loose` 是明确标记的宽松候选，不算精确命中。
- 页段默认 `--page-mode contained`，对象全部记录页码必须落在范围内。显式使用 `intersect` 才返回跨界对象，并列出范围外页码。未知页码不混入页段结果。
- 返回完整表格、公式或段落，不编造行列语义。关联上下文默认只给引用，`--expand-context` 才展开并标识范围外内容。
- `--revision rev-000001` 查询已发布历史快照；每次查询固定一个版本。

## 来源查询

```bash
python3 scripts/pdf2md.py trace --workspace /data/organized --unit-id u-ID
python3 scripts/pdf2md.py trace --workspace /data/organized --unit-id u-ID \
  --input /data/manual.pdf --render-dir /data/new-evidence
python3 scripts/pdf2md.py source-visibility --workspace /data/organized --input /data/manual.pdf
```

裁图需要哈希一致的 PDF，只写工作区、原结果和源 PDF 之外的新目录。坐标为未旋转页面左上角 PDF points，页码从 1 开始。多个候选全部保留，未映射对象不删除。

`ASSOCIATED_NATIVE_LAYOUT` 不证明技术内容正确。修订后仍可沿用来源位置，但不继承旧文本匹配结论。可见性报告仅说明源文本块的几何关联，不把未映射直接判断为遗漏或覆盖。

## 局部修订

准备含目标对象完整新内容的 UTF-8 文件，提出候选：

```bash
python3 scripts/pdf2md.py revise propose --workspace /data/organized \
  --unit-id u-ID --operation replace --text-file /data/corrected.txt \
  --reason "Restore source expression" --proposer editor
```

返回实际候选路径和 `expected_revision`。可用 `--source-evidence /data/evidence.json` 附加来源区域数组，字段包含已保留候选中的 `input_sha256`、`page`、`bbox`。字段验证是来源绑定，不自动证明修改正确。

```bash
python3 scripts/pdf2md.py revise check --workspace /data/organized \
  --proposal /data/organized/proposals/proposal-ID.json
python3 scripts/pdf2md.py revise apply --workspace /data/organized \
  --proposal /data/organized/proposals/proposal-ID.json --expected-revision rev-000001
```

提交校验工作区版本、旧内容哈希、修改前内容、结构类型和资源。JSON 候选可含少量明确 Unit 的 `operations`，不提供全书正则替换。一次事务只有一个写入者，完整新快照校验后才原子更新 HEAD。失败记录和临时产物保留，旧版本继续可读。

调整归属不改正文：

```bash
python3 scripts/pdf2md.py revise propose --workspace /data/organized \
  --unit-id u-ID --operation move --target-scope scope-ID \
  --reason "Adjust local organization" --proposer editor
```

解释、推导或补充使用 `--operation annotate --text-file /data/note.txt`，另存为派生注释，不伪装为原文。注释记录依赖版本，原内容改变后标为待复核。当前注释支持解释文字，不新增图片或资源链接。

普通修订保留 Unit ID，正文变化递增内容修订号；仅移动保留内容哈希，递增结构修订号。标题改名同步导航。标题层级改变、对象拆分、合并或类型改变当前明确拒绝；将来必须分配新身份并保留旧新替代关系。

显式提交不产生 `HUMAN_VERIFIED`。手工编辑封存文件会被校验拒绝，不自动接受为新的规范正文。

## 历史、回滚与导出

```bash
python3 scripts/pdf2md.py revise history --workspace /data/organized
python3 scripts/pdf2md.py revise rollback --workspace /data/organized \
  --to-revision rev-000001 --expected-revision rev-000002 --reason "Undo local revision"
python3 scripts/pdf2md.py export --workspace /data/organized --format split --output-dir /data/new-split
python3 scripts/pdf2md.py export --workspace /data/organized --format single --output-dir /data/new-single
```

回滚发布新版本并保留历史，恢复目标正文、结构和来源；内容修订号不倒退。导出从规范正文生成，不应独立修改后再混为规范版本。校验覆盖正文、索引、来源、资产、引用及版本绑定，不认证全文语义。

完整参数见对应命令 `--help`。既有转换和 `trace --output-dir` 保持兼容。

## 自适应阅读打包

已有工作区文件过碎时，先生成独立阅读副本，不修改 HEAD 或历史修订：

```bash
python3 scripts/pdf2md.py export --workspace /data/organized --format reading \
  --input /data/manual.pdf --output-dir /data/new-reading
python3 scripts/pdf2md.py verify-reading --output-dir /data/new-reading
```

本轮新增打包参数按 Linux 入口验证，不扩大 Windows 启动器适配。`--input` 可省略；提供时必须是哈希一致的原 PDF。编号与标题的组合需要来源文本、位置和字体证据，并保留原始 Unit 和可逆展示记录。歧义不强行合并，结构精化不会把原来未映射的对象变成“已定位”。

阅读副本包含 `content/reading-NNN.md`、图片、导航、Unit/Scope/引用索引、来源映射、`reading_plan.json`、粒度统计和封存清单。一个文件可包含多个 Scope，较长 Scope 也可跨文件。优先保留完整对象和有来源证据的章节边界，再考虑自适应软长度；可用 `--target-chars` 指定软目标，但不会截断表格、公式或容器。调大原导入参数 `--soft-chars` 并不能合并已存在的标题碎片文件。

检查阅读结果后，可显式将方案发布为新的结构修订：

```bash
python3 scripts/pdf2md.py revise repackage --workspace /data/organized \
  --reading-export /data/new-reading --expected-revision rev-000001 \
  --reason "Adopt checked reading layout"
python3 scripts/pdf2md.py verify --workspace /data/organized
```

`--expected-revision` 使用 `verify` 返回的实际当前版本。源版本变化、工作区不匹配或导出被修改时拒绝发布。试验可在独立工作区副本中进行；发布仍经过写入锁、完整校验和原子 HEAD 更新，不重写旧修订目录。未修改 Unit 的内容哈希和内容修订号不变，文件路径、区间和结构修订号可更新。

检索和来源查询仍使用 Scope/Unit ID，不能用文件边界代替范围。组合标题新增父 Scope，历史 Scope 的成员集合保留；确认的层级差异另存来源证据，不静默改写旧查询含义。之后授权的移动操作可显式修改归属。局部修订沿用打包策略；组合标题内容改变时失效旧展示证据。回滚可以恢复聚合布局或原拆分布局，并保留修订历史。
