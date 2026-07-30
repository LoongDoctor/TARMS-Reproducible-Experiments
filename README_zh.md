# TARMS 实验复现说明

本目录把证据分成三个互不替代的层次：Python 原语与本地计算、AAMOS-00 公开匿名载荷上的受控协议一致性实验，以及仅经单元测试的 Hyperledger Fabric 实现脚手架。所有正式运行均写出带 SHA-256 的 manifest。正式绘图会拒绝 `fixture` 或 `simulated` provenance；fixture 图带有不可移除的 “NOT FOR SUBMISSION” 水印。

## 1. 环境

- Python 3.12；锁定依赖见 `requirements-lock.txt`。
- Node.js 20 或更高；链码与 Gateway 依赖由各自的 `package-lock.json` 锁定。
- Fabric：2.5.16；Fabric CA：1.5.17；Docker 主机；官方 `fabric-samples` 测试网络。

开发与发布制作环境按以下方式安装：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-lock.txt
npm --prefix fabric/chaincode ci --ignore-scripts --no-audit --no-fund
npm --prefix fabric/client ci --ignore-scripts --no-audit --no-fund
mkdir -p ../releases/v6
make release-test-report REPORT=../releases/v6/test-report.json
```

安全发布顺序固定为：

```text
install -> release-test-report -> freeze source -> formal run ->
reproduce into isolated output -> seal new release -> read-only verify
```

机器测试报告通过后再冻结受控源码并开始正式运行。投稿图只能复现到显式、
隔离的输出目录：

```bash
make reproduce-figures OUTPUT_DIR=../releases/v6/figures
make seal-release \
  RELEASE_DIR=../releases/v6/public-release \
  RUN_DIR=../releases/v6/reproduced_results/processed/aamos/aamos-submission-20260729-v6-local \
  SNAPSHOT=../releases/v6/controlled-source.zip \
  TEST_REPORT=../releases/v6/test-report.json \
  FIGURE_DIR=../releases/v6/figures
```

只有带显式发布目录、正式运行目录、源码快照、测试报告和隔离图件目录参数的
`make seal-release` 可以创建发布 manifest 与公开包。

干净公开包的验证明确分为两个阶段。刚解压且尚未向目录安装任何内容时，先
核对密封产物和完整树：

```bash
make verify-public verify-tree
```

然后安装锁定依赖并执行可运行测试：

```bash
python3 -m venv .venv
. .venv/bin/activate
make install
make test-python test-node verify-shell
```

安装依赖会产生未密封的`.venv`或`node_modules`运行目录，因此不要在这个已经
修改的工作副本上再次执行完整树门禁；需要重新核对完整性时，应重新解压ZIP。
发布构建器在临时的新解压目录中执行同样的“先完整性、后安装测试”顺序。

## 2. 已完成的 Python 投稿级运行

```bash
python3 scripts/run_python_benchmarks.py --profile submission
python3 scripts/run_conformance.py --repetitions 200
python3 scripts/run_component_conformance.py --repetitions 200
python3 scripts/make_figures.py --figure python --mode submission
python3 scripts/make_figures.py --figure component --mode submission
python3 scripts/make_figures.py --figure window --mode submission
```

当前交付记录包含：

- 6 个批量规模、6 个阶段、每格 200 次，共 7,200 条 Python 微基准观测；
- 6 类 late-update root-conformance 用例、每类 200 次，共 1,200 条；
- 11 类组件一致性用例、每类 200 次，共 2,200 条；
- 四个投稿图族及逐图 source-data CSV。

原始观测位于 `results/raw/`，统计汇总位于 `results/processed/`，图件位于 `results/figures/submission/`。不要跨证据层比较吞吐：Python `signature_admission_batch` 只测签名验证与内存接纳状态机，不是 Fabric TPS，也不是完整 `VerifyCurrent`。

## 3. Fabric 2.5.16 实测

以下命令只能在安装了 Docker 且可获取官方 Fabric 镜像的主机上执行。本交付环境没有 Docker，因此稿件中没有 Fabric 性能数值。

```bash
export FABRIC_SAMPLES_DIR=/absolute/path/to/fabric-samples
bash fabric/network/bootstrap.sh
PROFILE=smoke bash fabric/network/run_experiments.sh
PROFILE=submission bash fabric/network/run_experiments.sh
bash fabric/network/teardown.sh
```

submission profile 包含 5 个独立轮次、每用例 10 s 预热和 60 s 测量；测量 anchor submit、query、并发吞吐及热键 CAS/MVCC 冲突。`run_manifest.json` 的 provenance 必须为 `measured_fabric`，否则投稿绘图门禁会拒绝生成正式图。

真实日志产生后运行：

```bash
python3 scripts/make_figures.py --figure fabric --mode submission
python3 scripts/make_figures.py --figure late --mode submission
```

## 4. AAMOS-00 二次分析

从 University of Edinburgh DataShare 下载 DOI
`10.7488/ds/3775` 的 AAMOS-00 文件，并把下列五个文件放在同一目录：

- `aamos00_data_dictionary.xlsx`
- `anonym_aamos00_dailyquestionnaire.csv`
- `anonym_aamos00_peakflow.csv`
- `anonym_aamos00_smartinhaler.csv`
- `anonym_aamos00_weeklyquestionnaire.csv`

正式运行固定为 20 个 RNG seeds、四档非零注入率、14 个 constructed
invalid scenarios、7 个 boundary controls、12 条实验管线和 2,000 次
crossed seed-participant bootstrap。提交 profile 会核对配置身份、文件名、
SHA-256、22 名参与者、1,583 个 daily-questionnaire participant-days 和
1,582 个完整三项 symptom-count days：

```bash
python3 scripts/run_aamos_standard_enhanced.py \
  --source-dir /absolute/path/to/aamos00 \
  --output-root reproduced_results \
  --profile submission \
  --bootstrap-reps 2000 \
  --bootstrap-seed 20260722 \
  --run-id aamos-submission-R4

python3 scripts/make_figures.py \
  --figure aamos \
  --mode submission \
  --aamos-run reproduced_results/processed/aamos/aamos-submission-R4 \
  --output reproduced_results/figures/submission
```

运行产生 12 个 manifest-whitelisted canonical artifacts；隐藏 staging
文件、原始 AAMOS 文件和 participant-keyed decision tables 不进入普通
Git 仓库。公开仓库发布汇总、图源、代码和哈希清单，并记录本地可重建
但未发布的 canonical artifacts 的 SHA-256。

实验实现分为三个接口：六谓词 returned-record verifier、独立
admission-slot evaluator，以及 successor-transition validator。`all_checks`
是简化实验配置，不是完整论文级 `VerifyCurrent`。相同 admission slot
和相同 digest 是幂等重传；相同 slot 和不同 digest 才是 counter conflict。

## 5. 统计与真实性约束

- 延迟报告 median、IQR、P95 和 median bootstrap 95% CI；原始重复不聚合丢弃。
- AAMOS 使用 crossed seed-participant bootstrap；每次 replicate 的 participant multiplicities 在全部抽中 seed occurrences 间共享。
- AAMOS 报告 `n/N`、coverage、abstention、covered-output agreement、upward discordance 和 symptom-count loss。
- 合成密钥、证书、签名、`boot`、`ctr`、绑定与攻击标签不应描述为 AAMOS 原生字段。
- 确定性 conformance 用 `n/N`，不进行无意义的显著性检验，也不解释为现实攻击检测率。
