# Pdf2Md

[English](README.md) | [简体中文](README.zh-CN.md)

[更新日志](CHANGELOG.md)

版本：**0.1.0a2**。通过命令行在本地离线将原生电子 PDF 转成 `manual.md + assets/`。

## 快速开始

首次使用请完成[环境安装](docs/CONFIGURATION.zh-CN.md#环境安装)。激活 Python 环境后，在本目录运行：

```bash
python scripts/pdf2md.py convert --input ../manual.pdf
```

Windows 完成一次 WSL2 环境准备后，可直接在 PowerShell 中运行，无需 Windows Python：

```powershell
.\scripts\pdf2md.ps1 convert --input "D:\Documents\manual.pdf"
.\scripts\pdf2md.ps1 batch --input-dir "D:\Documents\pdfs" --recursive
```

结果保存在 PDF 同目录的 `manual_pdf2md/` 中，打开其中的 `manual.md` 即可。移动结果时一起保留 `assets/`。

转换会自动执行环境检查、PDF 预检和输出完整性检查，无需另行运行 `doctor`、`preflight` 或 `verify`。

## 常用操作

```bash
# 指定输出目录
python scripts/pdf2md.py convert --input ../manual.pdf --output-dir ../result

# 中断后继续
python scripts/pdf2md.py convert --input ../manual.pdf --resume

# 批量转换，包含子目录
python scripts/pdf2md.py batch --input-dir ../pdfs --recursive

# 查看完整参数
python scripts/pdf2md.py convert --help
```

## 默认设置

| 配置 | 默认行为 |
| --- | --- |
| GPU | 第一个可见 GPU，自动确定设备节点 |
| 后端 Python | 当前 Python 环境 |
| 模型目录 | 源码目录旁的 `models/` |
| 输出目录 | 输入旁的 `<文件名或目录名>_pdf2md/` |
| 网络 | 离线，无需填写 `--offline` |
| 分块与重试 | 每块 50 页，失败重试 1 次 |
| 配置文件 | 自动查找，也可完全不创建 |

输入位于源码目录内时，默认输出改放到源码目录旁的 `pdf2md-output/`，保持源码与结果分离。已有输出不会被覆盖；续跑需使用 `--resume`，并保持输入及环境不变。

修改模型路径、使用独立后端环境、多卡或 MIG 等进阶设置，见[配置与故障排查](docs/CONFIGURATION.zh-CN.md)。

## 适用范围

原生 Linux 或单 NVIDIA GPU 的 WSL2、Python 3.12 和 bubblewrap。Windows 通过 WSL2 执行，不使用原生 Windows 推理。支持原生电子 PDF，包含其中的纯矢量图页；不支持扫描文字或 OCR。后端为 MinerU 4.0.12，使用锁定的离线模型。多 GPU、MIG 仅支持原生 Linux。

## 结果检查

`SUCCESS_WITH_WARNINGS` 表示输出完整性检查通过且存在质量告警，可在 `quality_report.json` 中查看详情。核对结果时重点关注公式运算符、数值与符号、表格列和条件归属、旋转标签及局部文本遗漏。详见[输出说明](docs/QUALITY.md)。

## 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 许可

部署和分发需分别核对 MinerU、PyMuPDF、模型及源文档的许可条件。详见[许可说明](docs/QUALITY.md#licensing)。
