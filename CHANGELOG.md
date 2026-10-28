# Changelog

## D1 d1.1 - Local Candidate

- 新增独立 `organize` 入口，读取已有封存结果，保存稳定 Unit、显式/推断/虚拟 Scope 及可逆多文件正文，不重跑解析器。
- `search` 先限定 Scope、页段和类型，再进行精确标识或文本检索；`trace --workspace` 提供来源查询及按需裁图。
- 局部候选支持正文替换、结构归属调整和独立派生注释；显式提交采用版本/哈希冲突检查、单写入者及原子 HEAD 发布。
- 修订同步正文、索引、来源、引用和依赖状态；回滚保留全部历史，导出由规范正文生成。
- 保持现有转换、Worker、模型绑定与历史产物不变；模块版本与下列转换产品版本分别记录。

## 0.1.0a5 - Candidate

- 新增标准库 `setup`：只读检查、一次授权、在线安装和离线导入，分别返回 READY / NEEDS_APPROVAL / NEEDS_ACTION / FAILED。
- 独立用户级 CPU 或 GPU 协调器/解析器环境；依赖保持锁定，不安装 Python、不修改系统环境。安装日志、断点及并发锁独立保存。
- GPU 模型使用固定 Hugging Face revision 和原有逐文件 SHA256；全部验证后才生成完成标记。支持下载续传及校验后的离线复制。
- Debian/Ubuntu 可从受信任的 APT 包元数据下载并校验 bubblewrap，用户级解包，不执行系统安装。
- 真实合成 PDF 转换、正式校验与来源查询通过后才原子发布配置；普通 Linux/Windows 入口自动找到协调器。
- Skill 增加新会话只读检查、一次授权后继续原任务的流程。转换保持离线，GPU 安装失败不切换 CPU。
- 不改变 Worker、模型绑定、转换和质量算法；完整验收矩阵与尚未关闭的门禁另存于仓库外 artifacts。

## 0.1.0a4 - 2026-10-25

### Windows Startup

- 新增 `scripts/pdf2md.cmd`，作为 Windows 用户和 Agent 的默认入口；无需 Windows Python。
- 在常见的 PowerShell `Restricted` 策略下启动已有 WSL2 桥接，仅对子进程使用执行策略参数，不修改用户、系统或组织策略。
- 保留参数、工作目录、中文空格路径及退出码；原 `.ps1` 入口保留兼容。
- WSL 不可用或选定 Python 不存在时，提供明确的部署提示，不自动安装依赖或修改安全设置。
- 中英文 README、配置说明与 Skill 统一采用 CMD 入口；明确克隆源码不等于已经安装运行环境和模型。
- 保持解析 Worker、模型、后端选择和质量算法不变；新版本继续要求来源映射，旧版结果仍可独立校验。

### Validation

- WSL 的 GPU/CPU 两个环境各运行 153 项回归：各 148 项通过，5 项 Windows 专属测试跳过；Windows 下 7 项启动入口测试通过。
- 在 `Restricted` 父进程、中文/空格/特殊字符目录中，验证正常产品入口的帮助、版本、配置发现、错误退出码与明确的部署提示。
- 已准备环境中，默认 GPU 完成一页非敏感合成 PDF 转换及正式校验；显式 CPU 路径也通过。未重跑 UPA5585 全文，未改动冻结演示成品。
- 本轮验证的是产品启动入口与实际转换，不把命令行测试宣称为新的 Agent Prompt 端到端验收。

## 0.1.0a3 - 2026-10-24

### CPU And GPU Selection

- 新增 `--compute auto|cpu|cuda`，默认优先使用就绪的 GPU，否则使用已准备好的 CPU 后端。
- CPU 使用 PyMuPDF4LLM / PyMuPDF / Layout 1.28.2，关闭 OCR；独立锁定依赖，不要求 CUDA 或 MinerU。
- 支持 `cpu_python`、`--cpu-python` 和 `PDF2MD_CPU_PYTHON`，可与原 GPU 环境分开部署。
- `doctor` 分别报告后端可用性、实际选择及原因。批次固定后端，运行失败不切换；续跑和缓存绑定后端、依赖与环境。
- CPU Worker 保留网络、PID 和只读输入隔离，不挂载 GPU；GPU Worker、模型和质量检查算法保持不变。

### Verifiable Source Mapping

- 默认生成 `source_map.json`，绑定原 PDF、最终 Markdown 和图片哈希。
- 为输出对象记录 ID、类型、最终字符区间、行号、物理页码、PDF points 区域及关联依据。
- 新增 `trace`，支持按对象 ID 或 Markdown 行号返回 JSON；可指定原 PDF 和外部目录，按需导出来源区域截图。
- 区分原生布局关联、独立原文匹配、歧义和未定位状态；关联成功不代表内容正确。
- 映射完整性纳入 `verify`；旧结果继续按原规则校验，不补写映射。
- 同步更新中英文上手文档、配置说明及 Skill，无需可视化页面。

### Validation And Scope

- 本机 CPU 和 GPU 环境各通过 143 项测试；PowerShell 5.1 的 12 项入口用例通过。
- 完成合成 PDF 和公开 RT9013 手册的 CPU 转换、批量、恢复、来源查询及截图核验。
- GPU 小样本 Markdown 和图片与 a2 逐字节一致。
- 来源映射为对象级，不覆盖单元格或数学符号级语义；重复或不可靠来源保留未决状态。
- CPU 与 GPU 的转换质量不等同；本轮未执行原生 Linux GPU 实机回归或另一台无 GPU 物理机验证。

## 0.1.0a2 - 2026-10-24

### WSL2 And Windows Entry

- 支持单 NVIDIA GPU 的 WSL2 环境，自动识别 `/dev/dxg` 和 WSL 驱动库。
- 新增 PowerShell 启动入口 `scripts/pdf2md.ps1`，通过 WSL2 执行转换、批量和校验，无需 Windows Python。
- 支持中文、空格和 Windows 相对路径，以及发行版、Python 和 bubblewrap 路径覆盖。
- 平台、驱动和隔离工具纳入运行绑定；续跑与缓存检查环境一致性。
- 保留 Worker 的网络、PID 和只读文件系统隔离；原 GPU Worker、模型和解析策略不变。

### Validation And Scope

- 本机 Ubuntu 24.04 / WSL2：109 项测试通过；PowerShell 5.1 的 17 项入口用例通过。
- 完成真实 GPU 转换、结果校验、批量失败继续、任务恢复及隔离验证。
- Windows 通过 WSL2 运行，不支持原生 Windows 推理、WSL 多 GPU、MIG 或 UNC 路径。
- 本轮未执行原生 Linux GPU 实机回归；保留既有 Linux 配置与命令构造测试。

## v0.1 - 2026-10-23

- 首次发布本地离线 PDF 转 Markdown 工程原型。
- 提供转换、预检、批量、校验、中断恢复和原生解析缓存入口。
