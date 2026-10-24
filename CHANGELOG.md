# Changelog

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
