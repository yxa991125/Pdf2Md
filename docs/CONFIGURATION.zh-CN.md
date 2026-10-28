# 安装与配置

[English](CONFIGURATION.md) | [返回 README](../README.zh-CN.md)

## 环境安装

### 用户级初始化

Linux 运行 `python3 scripts/pdf2md.py setup`，Windows 运行 `.\scripts\pdf2md.cmd setup`。终端展示方案并询问一次；Agent 先运行 `setup --check --json`，获得授权后改用 `setup --yes`。非交互环境没有 `--yes` 时不会安装；`--check` 即使同时带有 `--yes` 也只读检查。

| 状态 | 退出码 | 含义 |
| --- | --- | --- |
| READY | 0 | 选定环境可用 |
| NEEDS_APPROVAL | 3 | 需要授权安装 |
| NEEDS_ACTION | 4 | 系统前提或手工配置需要处理 |
| FAILED | 1 | 安装或验证失败，保留日志 |
| 参数错误 | 2 | 命令参数无效 |

JSON 写 stdout，进度与安装日志写 stderr。完整参数见 `setup --help`。不会覆盖已有手工配置；配置优先级保持为显式参数、`PDF2MD_CONFIG`、当前目录/项目的 `runtime.local.json`、用户配置。损坏配置明确报错，不跳过它寻找另一个环境。

默认目录遵循 XDG：数据为 `~/.local/share/pdf2md`，日志与凭据为 `~/.local/state/pdf2md`，配置为 `~/.config/pdf2md/runtime.json`。WSL 环境默认建在 Linux 文件系统，而非 Windows 挂载盘。完成后无需激活环境。失败后重复原 setup 命令即可继续准备同一受管理环境；并发安装会被拒绝，不改动已有转换结果。

`auto` 有可见 GPU 时准备 GPU，否则准备 CPU；GPU 安装失败不切换 CPU。GPU 的协调器与解析器分开，CPU 使用独立兼容环境。原冻结清单中的 `pip==25.0.1` 属于安装器，不参与运行依赖安装；其余运行依赖锁定值（含 setuptools）保持不变。在线依赖来源为 PyPI；模型仅从 `config/model_download.json` 固定的 Hugging Face revision 获取，并逐文件核对原 SHA256，全部有效后生成完成标记。Python 包大小在 pip 解析前未知，GPU 依赖通常需要数 GiB；检查结果给出精确模型字节数。转换本身不访问上述服务。

离线导入示例，资源路径由用户明确提供：

```bash
python3 scripts/pdf2md.py setup --check --json --compute cuda --offline \
  --wheelhouse /media/wheels --wheelhouse /media/more-wheels --models /media/models
# 授权后将 --check 换成 --yes，其余选项保持一致。
```

Wheel 介质须覆盖 Python 3.12/Linux 和当前架构下的锁定依赖。GPU 模型目录包含 `MinerU-4_models_torch/`，也可直接指向该目录；校验后复制到用户级受管理目录，不修改源介质。CPU 无需 GPU 模型。可指定 `--bwrap /path/to/bwrap`，或在 wheelhouse 中提供发行版的对应 `.deb`。Debian/Ubuntu 缺少 bwrap 时，可从官方受信任 APT 元数据获取包并校验 SHA256，在用户目录解包，不执行 sudo、系统安装或包维护脚本。其他发行版须自行准备 bwrap。

系统前提为 Linux/WSL2、带 venv/ensurepip 的 Python 3.12、所需动态库及可用的用户/网络命名空间。不会自动下载 Python 或修改安全策略。Ubuntu 24.04 可由管理员提供 `python3.12-venv`；Debian 13 应另备 Python 3.12，不使用系统 Python 3.13；不在 PATH 时用该解释器启动 setup。Windows 先确保 WSL2 发行版可启动（`wsl.exe --list --verbose`），可用 `--wsl-distro NAME` 选择。GPU 驱动和设备权限需要预先就绪。CMD 入口支持普通 Restricted 策略，组织强制策略需管理员处理。

发布配置前会完成依赖、隔离检查，两页合成 PDF 真实转换、正式校验及来源查询；GPU 额外检查真实 CUDA 运算证据。失败安装只保留日志，不成为默认环境；实现指纹变化后重新验证受管理环境，不通过改名发布虚拟环境。

### 手工部署

需要 Linux/WSL2、Python 3.12 和支持用户/网络命名空间的 bubblewrap（`bwrap`）。GPU 路径另外需要 NVIDIA CUDA 支持；Windows 使用 PowerShell 的 WSL2 入口。

### 仅安装 CPU 环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-cpu.txt
python3 scripts/pdf2md.py convert --input ../manual.pdf
```

该环境同时包含协调器与锁定的 CPU 后端，不需要 CUDA、MinerU 或 GPU 模型目录。锁定版本的 Layout wheel 自带模型资源，转换时不下载权重。离线安装可在安装命令中增加 `--no-index --find-links /path/to/wheelhouse`。

### GPU 环境

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

## Windows 与 WSL2

在 WSL2 内一次性准备 Python 3.12、锁定依赖、模型和 bubblewrap。使用支持 WSL CUDA 的 Windows NVIDIA 驱动，不在 WSL 内安装 Linux 显示驱动。本轮 WSL 仅支持单 NVIDIA GPU，多 GPU 和 MIG 会被拒绝；原生 Linux 保留既有设备及 MIG 配置。

```powershell
.\scripts\pdf2md.cmd convert --input "D:\Documents\manual.pdf"
.\scripts\pdf2md.cmd verify --output-dir "D:\Documents\manual_pdf2md"
.\scripts\pdf2md.cmd doctor --wsl-distro Ubuntu-24.04 --wsl-python /path/to/venv/bin/python
```

默认使用系统默认 WSL 发行版，`--wsl-distro` 可覆盖。Python 选择顺序：`--wsl-python`、`PDF2MD_WSL_PYTHON`、所选或自动发现配置中的 `coordinator_python`、项目 `.venv/bin/python`、`python3`。项目 `.venv` 同时安装协调器和后端依赖、旁边放置 `models/` 时，无需配置文件。自定义路径也可使用选定的 WSL Python 通过 `init-config` 保存一次。入口不调用 Windows Python。

输入、输出、配置、模型、报告、缓存可使用盘符路径、Windows 相对路径或 WSL 绝对路径。命令行相对路径基于 PowerShell 当前目录，支持中文、空格和未创建的输出目录。首版拒绝 UNC 和 `D:manual.pdf` 这样的盘符相对路径，后者请写成 `D:\manual.pdf`。支持 `--key value` 和 `--key=value`；保留退出码和日志，转换成功后显示 Windows 可打开的结果路径。即使 PowerShell 的有效策略为 `Restricted`，也使用 `pdf2md.cmd` 启动。它只为自己的 PowerShell 子进程设置执行策略参数，不调用 `Set-ExecutionPolicy`，不修改注册表中的用户或系统策略。`.ps1` 保留兼容，但不再作为用户和 Agent 的默认入口。组织强制的组策略不会被覆盖；遇到这类限制应报告管理员，不修改安全设置。

bubblewrap 选择顺序：`--bwrap`、配置 `bubblewrap_path`、`PDF2MD_BWRAP`、PATH。用户目录中的可执行文件仅以单文件只读挂载，不挂载整个父目录。WSL 自动使用 `/dev/dxg` 和 `/usr/lib/wsl/lib`；设备、权限或驱动库缺失时明确失败。Worker 保留 `--unshare-all` 和新挂载的 `/proc`。环境指纹绑定平台、驱动清单、GPU UUID、bubblewrap 字节与版本、WSL libcuda 字节；环境、代码、配置或输入改变时使用新结果目录。a1 缓存不能直接复用于 a2。

## 运行时覆盖

`--compute auto` 为默认值，优先选择已准备好的 GPU，否则使用已准备好的 CPU 后端。`--compute cpu` 不使用 GPU 设备；`--compute cuda` 不会自动降级。配置损坏或绑定完整性校验失败会报错，不作为降级理由。运行中失败不切换后端；批次只选择一次后端。

已有 GPU 环境保持不变。增加 CPU 支持时，另建环境安装 `requirements-cpu.txt`，再通过配置 `cpu_python`、环境变量 `PDF2MD_CPU_PYTHON` 或 `--cpu-python /path/to/env-cpu/bin/python` 指定。优先级为命令行 > 配置 > 环境变量 > 协调器 Python。CPU-only 协调器使用锁定的 PyMuPDF 1.28.2；已有 1.26.0 协调器也可驱动独立的 1.28.2 CPU Worker。GPU 协调器仍保持 1.26.0。

`doctor` 分别报告 CPU/GPU 可用性与选择原因，不自动安装缺失后端。续跑和缓存复用固定原先后端及绑定，切换模式需换输出目录。原有重复 `--device /dev/...` 仍表示 GPU 节点授权，不用来选择 CPU。Windows 入口同步支持 `--cpu-python`、`--render-dir` 的路径转换及 `PDF2MD_CPU_PYTHON`。

```bash
python3 scripts/pdf2md.py convert --input ../manual.pdf \
  --models /path/to/models --parser-python /path/to/env-mineru/bin/python
```

`--gpu 1` 选择第二个可见 GPU，索引遵循 `CUDA_VISIBLE_DEVICES`；也可使用完整 GPU UUID。普通 GPU 自动检测设备节点。MIG 使用 `--gpu MIG-UUID`，并通过重复 `--device` 指定物理 GPU、`/dev/nvidiactl`、`/dev/nvidia-uvm` 和对应的 GPU-instance/compute-instance capability 节点。

## 保存设置

需要反复使用非默认路径时，可生成配置文件：

```bash
python3 scripts/pdf2md.py init-config \
  --parser-python /path/to/env-mineru/bin/python --models /path/to/models
```

默认写入当前目录的 `runtime.local.json`，不覆盖已有文件。以后在该目录运行时会自动读取。也可用 `--config /path/to/runtime.json` 指定配置。

配置查找顺序：显式 `--config`、`PDF2MD_CONFIG`、当前目录的 `runtime.local.json`、源码目录的同名文件、用户目录下的 `~/.config/pdf2md/runtime.json`（遵循 `XDG_CONFIG_HOME`）。未找到时使用内置默认值。显式指定的文件不存在时会报错。

设置优先级：命令行覆盖 > 配置文件 > 环境变量 > 内置默认值。环境变量支持 `PDF2MD_MODELS` 和 `PDF2MD_PARSER_PYTHON`。配置文件中的相对路径以该文件所在目录为基准；命令行和环境变量路径以当前目录为基准。配置示例见 `config/runtime.example.json`。

## 检查与恢复

```bash
# 只检查环境，不运行推理
python3 scripts/pdf2md.py doctor

# 只检查输入 PDF
python3 scripts/pdf2md.py preflight --input ../manual.pdf

# 重新检查已有结果的封存、页序和图片
python3 scripts/pdf2md.py verify --output-dir ../manual_pdf2md

# 批量中断恢复
python3 scripts/pdf2md.py batch --input-dir ../pdfs --recursive --resume
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
