# Changelog

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
