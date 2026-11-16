# Pdf2Md

[English](README.md) | [简体中文](README.zh-CN.md)

[更新日志](CHANGELOG.md)

版本：**0.1.0a9**（工程预览版）。在本地将原生电子 PDF 转为 Markdown，保留图片、提供来源定位并自动检查内容。支持 GPU 和 CPU 运行。

## 快速开始

给 Agent 一句提示即可：

> 使用 Pdf2Md Skill 转换 inputs/manual.pdf。

首次使用时，Agent 先检查环境、说明安装方案，获得一次确认后准备用户级环境并继续转换。手动初始化时，在本目录运行：

```bash
git clone https://github.com/yxa991125/Pdf2Md.git
cd Pdf2Md
python3 scripts/pdf2md.py setup
python3 scripts/pdf2md.py convert --input /path/manual.pdf
```

Windows / WSL2，无需 Windows Python：

```powershell
.\scripts\pdf2md.cmd setup
```

初始化后，使用 `python3 scripts/pdf2md.py convert --input ../manual.pdf`；Windows 使用 `.\scripts\pdf2md.cmd convert --input "D:\Documents\manual.pdf"`。无需每天激活环境或指定 Python。结果保存在 PDF 同目录的 `manual_pdf2md/` 中，移动 `manual.md` 时一起保留 `assets/`。

需要 Linux/WSL2 及支持 venv 的 Python 3.12。初始化不安装 Python、不修改系统设置；在线获取锁定依赖和绑定模型，也支持[离线导入与系统前提说明](docs/CONFIGURATION.zh-CN.md#环境安装)。文档转换始终离线。

转换会自动执行环境检查、PDF 预检和输出完整性检查，无需另行运行 `doctor`、`preflight` 或 `verify`。

GPU 环境可用时优先使用 GPU，否则使用已准备好的 CPU 后端。CPU-only 安装不需要 CUDA 或 MinerU；可通过 `--compute cpu` 或 `--compute cuda` 明确选择。

## 内容恢复

- **表格恢复**：对照 PDF 的单元格区域，将能够唯一定位的损坏表格恢复为可编辑的 HTML 表格，保留合并单元格及支持的上下标。
- **图形保留**：结合原始解析位置和 PDF 绘图区域，在边界可靠时保留包含标注与图题的完整源图，也处理图题位于边框外的情况。
- **来源定位**：将最终 Markdown 对象对应到 PDF 页码和区域，区分位于不同位置的重复表格。
- **技术检查**：对照原文核对支持的公式和数量；公式局部修复经过独立验证后应用。

这些处理随转换自动执行。GPU 解析仍使用 MinerU 4.0.12 Basic；内容恢复读取 PDF 原生文字和几何信息，无需额外部署模型，也不启用文字 OCR。源内容清点目前覆盖可识别的线框表格和明确的 Figure/Fig 图题，详见[检查范围与记录](docs/QUALITY.md)。

只做技术检查、不修复公式时使用 `--fidelity check`；跳过该阶段使用 `--fidelity off`。这两种模式仍保留源表格和图形检查，详见[技术检查与恢复](docs/FIDELITY.zh-CN.md)。

## 结果文件

默认转换结果包括：

```text
manual_pdf2md/
  manual.md                  Markdown 正文与表格
  assets/                    图片及原文区域截图
  source_map.json            输出与原 PDF 的位置对应
  source_preservation.json   表格、图形核对与恢复记录
  quality_report.json        页面、内容与技术检查汇总
  fidelity_assessment.json   公式与数量的详细核对结果
  fidelity_repair_log.json   局部修改候选与独立验证记录
  run_manifest.json          文件哈希及运行绑定
```

校验、来源查询和续跑时保留完整结果目录。阅读或交给后续程序处理时，将 `manual.md` 与 `assets/` 放在一起。技术检查文件由默认模式生成；结果中还保留运行与来源过程文件。

## 来源查询

```bash
python3 scripts/pdf2md.py trace --output-dir ../manual_pdf2md --line 25
```

`source_map.json` 为最终输出对象建立索引。查询返回 JSON，包括输出片段、原 PDF 页码和区域、原文及关联依据。截图按需生成，不附带可视化页面或全文截图。详见[来源映射说明](docs/SOURCE_MAPPING.zh-CN.md)。

## 常用操作

```bash
# 指定输出目录
python3 scripts/pdf2md.py convert --input ../manual.pdf --output-dir ../result

# 中断后继续
python3 scripts/pdf2md.py convert --input ../manual.pdf --resume

# 批量转换，包含子目录
python3 scripts/pdf2md.py batch --input-dir ../pdfs --recursive

# 查看完整参数
python3 scripts/pdf2md.py convert --help
```

## 默认设置

| 配置 | 默认行为 |
| --- | --- |
| GPU | 第一个可见 GPU，自动确定设备节点 |
| 计算模式 | `auto`：GPU 可用则优先使用，否则选择已准备好的 CPU 后端 |
| 后端 Python | 初始化保存的用户级环境；手工部署时使用当前环境 |
| 模型目录 | 初始化后使用已校验的用户级模型目录 |
| 输出目录 | 输入旁的 `<文件名或目录名>_pdf2md/` |
| 网络 | 离线，无需填写 `--offline` |
| 分块与重试 | 每块 50 页，失败重试 1 次 |
| 配置文件 | 自动查找，也可完全不创建 |

输入位于源码目录内时，默认输出改放到源码目录旁的 `pdf2md-output/`，保持源码与结果分离。已有输出不会被覆盖；续跑需使用 `--resume`，并保持输入及环境不变。

修改模型路径、使用独立后端环境、多卡或 MIG 等进阶设置，见[配置与故障排查](docs/CONFIGURATION.zh-CN.md)。

## 适用范围

Linux 或 WSL2、Python 3.12 和 bubblewrap。Windows 通过 WSL2 执行，不使用原生 Windows 推理。支持原生电子 PDF，包含其中的纯矢量图页；不支持扫描文字或 OCR。GPU 后端为 MinerU 4.0.12，CPU 后端为 PyMuPDF4LLM/Layout 1.28.2，两者结果可能不同。WSL GPU 路径仅支持单 NVIDIA GPU；多卡选择及 MIG 保留为原生 Linux 场景。

## 结果检查

```bash
python3 scripts/pdf2md.py verify --output-dir /path/manual_pdf2md
```

`quality_report.json` 分别列出以下内容：

| 项目 | 含义 |
| --- | --- |
| `page_processing` | 已处理的物理页数及页面覆盖率 |
| `content_preservation` | 源网格一致、表格重建、图形源区域保留及待核对位置 |
| `fidelity` | 技术属性的已检查、一致、已修复和未决数量 |

`verify` 校验文件哈希、页序、图片引用与解码、来源映射及检查记录。出现 `CONTENT_REVIEW_REQUIRED` 时，转换和校验返回退出码 2，保留结果及具体的表格或图形待核对项。页面覆盖率描述页面处理情况，不作为全文内容保留率。详见[输出说明](docs/QUALITY.md)。

## 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 许可

部署和分发需分别核对 MinerU、PyMuPDF、模型及源文档的许可条件。详见[许可说明](docs/QUALITY.md#licensing)。
