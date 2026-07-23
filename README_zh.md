# TARMS 可复现实验仓库

[English README](README.md)

本仓库仅包含 TARMS 的实验复现材料：实验源码、最新实测数据、绘图代码、已生成图件、图件源数据、自动化测试，以及 Hyperledger Fabric 实现脚手架。论文正文、投稿材料和内部修订记录均不在本仓库中。

## 一、仓库包含什么

| 证据层 | 运行编号 | 规模 | 可支持的解释 |
|---|---|---:|---|
| Python 微基准 | `python-20260723T020649Z` | 7,200 条观测 | 本地密码原语与算法耗时 |
| Late-update 一致性 | `conformance-20260723T020659Z` | 1,200 次执行 | 预设根重构规则的一致性 |
| 组件一致性 | `components-20260723T020700Z` | 2,200 次执行 | 签名、AcceptOnce、Merkle 与 latest-CAS 用例 |
| 载荷/窗口模型 | 图件源数据 | 6 个窗口设置 | 明确假设下的应用层载荷与等待时间模型 |
| Fabric 实现 | 单元测试源码 | 链码 6 项、客户端 9 项 | 交易接口与状态转换语义 |

需要严格区分：

- 本仓库没有真实 Fabric 网络性能数据；
- Python `signature_admission_batch` 只测验签与内存接纳状态机，不是 Fabric TPS；
- 一致性实验使用构造用例，不应解释为临床准确率、攻击检测率或端到端系统性能；
- 614 B 是特定 JSON 字段和编码假设下的应用层 `anchor + latest` 载荷，不是 Fabric 账本实际占用。

## 二、最短复现路径

环境要求：

- Python 3.12；
- Node.js 20 或更高；
- GNU Make；
- macOS 或 Linux。Windows 建议使用 WSL2。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make install
make verify
```

预期门禁结果：

- Python 测试 49 项；
- Fabric 链码测试 6 项；
- Fabric Gateway 客户端测试 9 项；
- 三个运行清单中的 7 个数据文件 SHA-256 全部匹配；
- 三组正式图件重新生成。

## 三、重新运行实验

默认将新结果写入已被 Git 忽略的 `reproduced_results/`，不会覆盖随仓库发布的证据：

```bash
make benchmark
make conformance
make figures-rerun
```

完整微基准包含 6 个批量规模、6 个阶段、每格 200 次，共 7,200 条观测；两个一致性实验分别生成 1,200 条和 2,200 条记录。新运行会使用新的时间戳 run ID。

绝对耗时会随 CPU、操作系统、Python 构建和后台负载变化。复核时应先比较数据结构、预设结果是否一致和规模变化趋势，再比较绝对数值。

## 四、目录说明

```text
fabric/                       Fabric 链码、Gateway 客户端和网络脚本
scripts/                      基准、一致性、绘图和清单校验入口
src/tarms_experiments/        Python 实验实现
tests/                        Python 与 Fabric 静态测试
results/raw/                  最新原始观测和运行清单
results/processed/            统计汇总
results/figures/submission/   PDF/PNG 图件及 source-data CSV
docs/                         完整复现、证据边界和上传说明
```

详细操作见：

- [完整复现指南](docs/REPRODUCIBILITY.md)
- [实验证据登记表](docs/EVIDENCE_REGISTER.md)
- [GitHub 上传指南](docs/GITHUB_UPLOAD.md)

## 五、许可证与引用

本仓库采用 [Apache License 2.0](LICENSE)，引用信息见 [CITATION.cff](CITATION.cff)。
