# AAMOS-00受控协议一致性实验（R4冻结协议）

日期：2026-07-29

范围：作者批准的方案B，不包含真实Fabric网络性能实验。

## 1. 权威运行身份

R4正文、Fig. 6、公开复现候选包和审计文档只允许引用由
`public_release_manifest.json`声明、并与其正式`run_manifest.json`逐项绑定的
V6运行：

| 字段 | 冻结约束 |
|---|---|
| Run ID | 读取公开发布manifest中的`run_id` |
| Profile | `submission` |
| Bootstrap | `crossed_seed_participant_multinomial` |
| Repetitions / master seed | `2000` / `20260722` |
| Run manifest SHA-256 | 读取公开发布manifest中的`formal_run_manifest_sha256` |
| Controlled-source identity | 公开发布manifest、正式run manifest与受控源码快照三者一致 |
| 配置文件SHA-256 | `afdad4cef23c79307379e21011b1638ac91638f04b457dc21dc0be7888174baf` |
| 配置canonical SHA-256 | `37665ed58cb0e42242624a310351f7d3920e4815a7fe55f79848ec4f6557e1fe` |

Preview、fixture和任何更早运行只用于开发或历史对照，不得作为R4投稿证据。

## 2. 数据来源与分析母表

- 公开数据：AAMOS-00，DOI `10.7488/ds/3775`。
- 官方发布口径：22名参与者、2,054个至少一种模态有数据的patient-days。
- 本实验固定使用5文件子集：data dictionary、daily questionnaire、
  weekly questionnaire、peak-flow和smart-inhaler。
- 不分析environment、smartwatch、end-questionnaire和participant-info文件。
- 日问卷作为participant-day spine，得到1,583个唯一日问卷
  participant-days；1天缺少完整三项症状输入，得到1,582个eligible days。
- 三项症状计数0/1/2/3的天数为346/531/491/214。

读者侧名称固定为 **derived three-item symptom count**。它不是临床评分、
诊断、风险分层、治疗建议、临床结局或ground truth。

## 3. 合成协议层与三个评价器

AAMOS-00只提供匿名载荷和participant-day聚类。以下全部由实验合成：
设备身份、密钥、签名、设备状态、participant–device binding、admission
slot、Merkle对象、anchor/latest状态、requester context和场景标签。

实验严格区分：

1. **Returned-record verifier：** signature、device state、binding、Merkle
   membership、latest consistency和requester-context equality六个谓词。
2. **Admission evaluator：** 未占用slot可接纳；相同slot和相同digest为
   idempotent；相同slot和不同digest为counter conflict。
3. **Successor-transition validator：** 历史修改、删除、插入、合法迟到和
   canonical reorder。

`all_checks`只表示适用的实验检查组合，不是论文定义的完整
`VerifyCurrent`，也不包含authenticated ledger-state proofs、finalized
checkpoint acquisition、证书/时间相关生命周期证明、签名请求nonce与
expiry、latest authorization proof或Fabric执行。

## 4. 场景

### Returned-record constructed-invalid scenarios（10）

- `payload_after_signing`
- `wrong_device`
- `revoked_device`
- `binding_mismatch`
- `tampered_merkle_leaf`
- `tampered_merkle_path`
- `tampered_merkle_root`
- `stale_latest_pointer`
- `authorization_substitution`
- `mixed_attack`

### Admission constructed-invalid scenario（1）

- `counter_conflict`

### Successor-transition constructed-invalid scenarios（3）

- `historical_modification`
- `historical_deletion`
- `historical_insertion`

### Boundary controls（7）

- `idempotent_retransmission`
- `pre_signing_false_payload`
- `permanent_omission`
- `clinical_measurement_error`
- `incorrect_priority_rule`（内部稳定ID；读者侧称symptom-count rule）
- `legitimate_late_arrival`
- `canonical_reorder`

永久遗漏没有返回记录，因此没有returned-record decision。签名前虚假载荷、
测量误差和错误计算规则展示provenance不能证明生理或临床真实性。

## 5. 配置、抽样与规模

12个配置：

- `unverified`
- `signature_only`
- `signature_admission`
- `signature_binding_admission`
- `all_checks`
- `all_minus_signature`
- `all_minus_device`
- `all_minus_binding`
- `all_minus_admission`
- `all_minus_merkle`
- `all_minus_freshness`
- `all_minus_authorization`

20个固定seed为`20260722`至`20260741`。请求注入率为1%、5%、10%、20%；
每seed对应16、79、158、316个目标，实际率为1.011%、4.994%、9.987%、
19.975%。抽样固定participant和derived symptom count分层。

正式几何：

| 项目 | 行数 |
|---|---:|
| Clean configuration-evaluation rows | 379,680 |
| Constructed-invalid configuration-evaluation rows | 1,911,840 |
| Boundary configuration-evaluation rows | 265,440 |
| Total | 2,556,960 |
| Permanent-omission no-decision rows | 37,920 |
| Rows with a local experimental outcome | 2,519,040 |

159,320是14类违规在四档率、20个seed下的目标评估数，不是独立患者记录、
现实攻击或2,556,960条“verifier decisions”。

## 6. 指标与统计

必须报告：

- constructed-target rejection或transition block及准确`n/N`；
- clean false rejection；
- expected-stage agreement；
- `all_checks`与匹配all-minus-one配置的配对rejection risk difference；
- mixed-population coverage与abstention；
- covered symptom-count agreement；
- upward和symptom-count-loss discordance；
- requested与realized injection rate。

2,000次交叉两因子percentile bootstrap在每个replicate中分别对20个seed和
22个participant cluster有放回抽样；同一participant multiplicity vector
在全部抽中seed occurrences间共享。配对比较共享完整权重tensor。

区间只描述固定合成设计和源队列的重抽样变异，不是现实攻击prevalence、
患者总体或临床效果的置信区间。

## 7. 发布门禁

1. Fig. 6 source必须为235个marks，panel a/b/c/d分别为187/28/8/12。
2. 12项正式artifact必须与run manifest逐项SHA-256一致。
3. 公开包不重新分发AAMOS原始文件、派生participant-day表、participant-
   keyed manifests或逐判定大表；这些文件在公开manifest中记录canonical
   hash和本地重建理由。
4. 公开包必须排除隐藏staging/partial文件、root fixture、缓存、
   `node_modules`和编译临时文件。
5. 正文只允许implementation conformance、controlled
   integrity–coverage和capability-boundary结论。
