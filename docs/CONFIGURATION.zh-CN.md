# 安装与配置

[English](CONFIGURATION.md) | [返回 README](../README.zh-CN.md)

## 环境安装

需要 Linux、Python 3.12、NVIDIA 驱动和支持用户/网络命名空间的 bubblewrap（`bwrap`）。

可在一个 Python 环境中同时安装协调器依赖和 MinerU 后端；已有独立后端环境也可复用。协调器依赖见 `requirements-coordinator.txt`，后端参考清单见 `config/s1-mineru-requirements.lock.txt`。后端检查的版本包括 MinerU 4.0.12、torch 2.14.1、torchvision 0.29.1、pypdfium2 5.14.0；安装包需匹配目标 GPU 和驱动。

从本地 wheelhouse 安装协调器依赖示例：

```bash
python3.12 -m venv ../env-coordinator
source ../env-coordinator/bin/activate
python -m pip install --no-index --find-links /path/to/wheelhouse -r requirements-coordinator.txt
python -m pip check
```

此步骤只安装协调器依赖；MinerU 后端及其依赖需另外准备。将 `config/baseline_bindings.json` 对应的模型文件放在以下位置，运行时会校验哈希：

```text
workspace/
  Pdf2Md/
  models/
    MinerU-4_models_torch/
      ...
```

模型和依赖不会在转换时自动下载。环境准备完成后，日常转换只需指定输入 PDF。

## 临时覆盖

```bash
python scripts/pdf2md.py convert --input ../manual.pdf \
  --models /path/to/models --parser-python /path/to/env-mineru/bin/python
```

`--gpu 1` 选择第二个可见 GPU，索引遵循 `CUDA_VISIBLE_DEVICES`；也可使用完整 GPU UUID。普通 GPU 自动检测设备节点。MIG 使用 `--gpu MIG-UUID`，并通过重复 `--device` 指定物理 GPU、`/dev/nvidiactl`、`/dev/nvidia-uvm` 和对应的 GPU-instance/compute-instance capability 节点。

## 保存设置

需要反复使用非默认路径时，可生成配置文件：

```bash
python scripts/pdf2md.py init-config \
  --parser-python /path/to/env-mineru/bin/python --models /path/to/models
```

默认写入当前目录的 `runtime.local.json`，不覆盖已有文件。以后在该目录运行时会自动读取。也可用 `--config /path/to/runtime.json` 指定配置。

配置查找顺序：显式 `--config`、`PDF2MD_CONFIG`、当前目录的 `runtime.local.json`、源码目录的同名文件、用户目录下的 `~/.config/pdf2md/runtime.json`（遵循 `XDG_CONFIG_HOME`）。未找到时使用内置默认值。显式指定的文件不存在时会报错。

设置优先级：命令行覆盖 > 配置文件 > 环境变量 > 内置默认值。环境变量支持 `PDF2MD_MODELS` 和 `PDF2MD_PARSER_PYTHON`。配置文件中的相对路径以该文件所在目录为基准；命令行和环境变量路径以当前目录为基准。配置示例见 `config/runtime.example.json`。

## 检查与恢复

```bash
# 只检查环境，不运行推理
python scripts/pdf2md.py doctor

# 只检查输入 PDF
python scripts/pdf2md.py preflight --input ../manual.pdf

# 重新检查已有结果的封存、页序和图片
python scripts/pdf2md.py verify --output-dir ../manual_pdf2md

# 批量中断恢复
python scripts/pdf2md.py batch --input-dir ../pdfs --recursive --resume
```

`convert --native-cache /path/to/previous-result` 可复用本工具生成且兼容的封存分块，跳过对应推理。续跑和缓存复用均校验输入、实现、配置及环境绑定；输入集合发生变化时使用新的输出目录。

批量任务顺序执行，单份失败后继续。`batch_summary.json` 和 `logs/` 保存结果与诊断；`effective_runtime.json` 固定该批次实际使用的设备和路径。存在失败项时退出码为 2。

## 故障排查

| 问题 | 处理方式 |
| --- | --- |
| 找不到模型或哈希不匹配 | 检查 `--models` 指向的根目录及绑定清单 |
| 后端依赖缺失或版本不符 | 激活正确环境，或通过 `--parser-python` 指向后端环境 |
| 找不到 GPU 或设备不可访问 | 检查驱动、设备权限及 `CUDA_VISIBLE_DEVICES`；MIG 显式填写 UUID 与节点 |
| bubblewrap/命名空间检查失败 | 检查 `bwrap` 安装和系统用户/网络命名空间支持 |
| 输出目录已存在 | 原任务已停止且输入、配置及环境未变时用 `--resume`；其他情况换目录 |
| 扫描 PDF 预检失败 | 使用带原生文本层的 PDF；当前流程不执行 OCR |

完整参数见 `python scripts/pdf2md.py --help` 及各子命令的 `--help`。
