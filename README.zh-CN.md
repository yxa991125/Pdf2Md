# Pdf2Md

[English](README.md) | [简体中文](README.zh-CN.md)

[更新日志](CHANGELOG.md)

版本：**0.1.0a5**（候选版）。在本地离线将原生电子 PDF 转成 `manual.md + assets/`，并提供便于核验的来源定位。支持 GPU 和 CPU 运行。

## 快速开始

给 Agent 一句提示即可：

> 使用 Pdf2Md Skill 转换 inputs/manual.pdf。

首次使用时，Agent 先检查环境、说明安装方案，获得一次确认后准备用户级环境并继续转换。手动初始化时，在本目录运行：

```bash
python3 scripts/pdf2md.py setup
```

Windows / WSL2，无需 Windows Python：

```powershell
.\scripts\pdf2md.cmd setup
```

初始化后，使用 `python3 scripts/pdf2md.py convert --input ../manual.pdf`；Windows 使用 `.\scripts\pdf2md.cmd convert --input "D:\Documents\manual.pdf"`。无需每天激活环境或指定 Python。结果保存在 PDF 同目录的 `manual_pdf2md/` 中，移动 `manual.md` 时一起保留 `assets/`。

需要 Linux/WSL2 及支持 venv 的 Python 3.12。初始化不安装 Python、不修改系统设置；在线获取锁定依赖和绑定模型，也支持[离线导入与系统前提说明](docs/CONFIGURATION.zh-CN.md#环境安装)。文档转换始终离线。

转换会自动执行环境检查、PDF 预检和输出完整性检查，无需另行运行 `doctor`、`preflight` 或 `verify`。

GPU 环境可用时优先使用 GPU，否则使用已准备好的 CPU 后端。CPU-only 安装不需要 CUDA 或 MinerU；可通过 `--compute cpu` 或 `--compute cuda` 明确选择。

## 来源查询

```bash
python3 scripts/pdf2md.py trace --output-dir ../manual_pdf2md --line 25
```

`source_map.json` 为最终输出对象建立索引。查询返回 JSON，包括输出片段、原 PDF 页码和区域、原文及关联依据。截图按需生成，不附带可视化页面或全文截图。详见[来源映射说明](docs/SOURCE_MAPPING.zh-CN.md)。

## 文档组织与局部修订

已有完整转换结果可直接组织为多文件内容，不重新解析 PDF：

```bash
python3 scripts/pdf2md.py organize --from-result ../manual_pdf2md --workspace ../organized
python3 scripts/pdf2md.py scopes --workspace ../organized
python3 scripts/pdf2md.py search --workspace ../organized --query DATA_STATUS --match exact
```

没有目录也可使用虚拟 Scope。稳定 Unit 支持来源查询、局部修订、版本发布和回滚。原转换结果保持不变，详见[文档组织说明](docs/ORGANIZATION.zh-CN.md)。Windows 使用 `.\scripts\pdf2md.cmd`。

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

`SUCCESS_WITH_WARNINGS` 表示输出完整性检查通过且存在质量告警，可在 `quality_report.json` 中查看详情。核对结果时重点关注公式运算符、数值与符号、表格列和条件归属、旋转标签及局部文本遗漏。详见[输出说明](docs/QUALITY.md)。

## 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 许可

部署和分发需分别核对 MinerU、PyMuPDF、模型及源文档的许可条件。详见[许可说明](docs/QUALITY.md#licensing)。
