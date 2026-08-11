# Obsidian Skill Router 测试与发布计划

## 1. 测试目标

测试的对象是 Registry Compiler、Python Router、CLI 与只读 Hook 适配层。所有 Skill 都是合成或脱敏 fixture；真实 `.codex/skills` 只用于 `test_artifacts/` 下的本地 smoke test，绝不进入提交内容。

质量判定分成四层：发现是否准确、单 Skill 是否正确选择、多 Skill 计划是否完整有序、系统是否在风险或不确定时正确拒答。不能用总体均值掩盖任一层失败。

## 2. 测试分层

后续将按以下边界拆分测试文件；当前回归用例仍兼容 `tests/test_router.py` 和 `tests/test_router_v02.py`。

| 层 | 核心验证 |
|---|---|
| discovery | `SKILL.md` 精确发现、资源目录跳过、深度限制、manifest-only 迁移、warning、稳定 ID、source hash |
| search | exact/alias、BM25F、字符 n-gram、RRF、Package 软先验、短 token、中英混合、错字和 near-miss |
| policy | manual、explicit-only、profile-only、compatibility、negative trigger、topic/action risk 最小对照 |
| enrichment | approved/pending/rejected/stale、保护字段、source hash、candidate 不泄漏到默认路由 |
| graph | typed edge、方向与证据、candidate 隔离、三轮 PPR、top-20 一跳范围、8% lift 上限 |
| planner | 2–8 步最小覆盖、9 步以上贪心、依赖排序、环、歧义、无 Skill、风险升级、序列词误拆反例 |
| contracts | schema 0.1/0.2、五态到旧三态映射、单 JSON envelope、Hook/Python 深度等价和 route-only 边界 |
| pipeline | `scan → validate → route → eval`、staging/publish 故障恢复、managed file 所有权、源文件不变 |
| evaluation | held-out、真实 workload、red-team、意图族切分、策略消融、校准与质量门禁 |
| performance | 2,000 个程序化虚构 Skill 的冷/热 p50、p95、max 和 candidate payload |

测试继续使用 Python 标准库 `unittest`，不增加运行时依赖。

## 3. Fixture 规范

评测 case 应使用统一结构：

```json
{
  "id": "pdf_to_obsidian.variant_01",
  "intent_family": "pdf_to_obsidian",
  "query": "Extract PDF and then create an Obsidian note",
  "profile": "",
  "expected_decision": "skill_plan",
  "acceptable_primary_skills": ["pdf-extract"],
  "required_plan_skills": ["pdf-extract", "obsidian-note-writer"],
  "forbidden_skills": ["audio-transcribe"],
  "required_order": [["pdf-extract", "obsidian-note-writer"]],
  "expected_review": false,
  "forbid_graph_expansion": false
}
```

必须维护以下 fixture 族：

- registry tree：真实主路径、嵌套、资源 README、深度 6/7、跨 root 重名。
- search catalog：40–60 个相近虚构 Skill，覆盖 PDF/OCR/export、JD/resume、token/secret 等易混组。
- policy matrix：route mode × 意图形式 × profile × compatibility。
- risk minimal pairs：讨论 token 与传输 token、解释 shell 与执行 shell、读文件与写/删文件。
- multi-skill：线性链、分支、同一 Skill 覆盖多步、依赖环、风险步骤、局部无能力。
- graph：confirmed/candidate/rejected、方向反例、hub 噪声和过度扩展。
- enrichment：全部审核状态、过期 hash、非法保护字段。
- legacy 0.1：固定兼容快照。
- real workload：仅保存脱敏数据于被忽略的 `test_artifacts/`。

同义改写必须按 `intent_family` 整组切分，禁止同一意图家族同时进入调参与 held-out 集。

## 4. 指标定义

单 Skill 指标：Discovery Precision、Explicit Name Recall、Candidate Recall@3、Selective Precision@1、False Activation、Clarification Recall、Over-Clarification、No-Skill Recall。

多 Skill 指标：Step Coverage、Plan Skill Precision/Recall/F1、Dependency Satisfaction、Topological Validity、Extra Skill Rate、Plan Review Recall、Cycle Detection Recall。

图指标必须成对报告：Lexical Recall@3、Graph Recall@3、Graph Incremental Gain、Over-Expansion Delta、Candidate Leakage、Graph Lift Cap Violation。不能把图收益混入 lexical cross-package recall。

排序与校准报告 MRR、nDCG@5、宏平均 Package 指标、Brier、ECE 与 coverage-risk curve。Brier/ECE 只有在固定分数校准映射后才作为发布门禁。

成本指标使用真实序列化的 `primary + alternatives + plan` 计算 candidate payload；性能分别报告冷缓存和热缓存。

## 5. 消融矩阵

同一 held-out 与真实 workload 必须比较：

1. legacy heuristic。
2. exact + alias。
3. BM25F。
4. BM25F + char n-gram。
5. hybrid 去掉 Package prior。
6. hybrid + Package soft prior + RRF。
7. hybrid + graph shadow。
8. 仅离线测试的 negative gate / route-mode gate 关闭组。

每组报告配对差值。Graph 只有在 Top-3 增益为正且 over-expansion 不增加时才允许离开 shadow mode。

## 6. 发布门禁

- Skill 发现精度 100%。
- 显式 Skill 名召回 100%。
- Candidate Recall@3 不低于 90%。
- Selective Precision@1 不低于 85%。
- False Activation 不高于 1%。
- High-risk Review Recall 100%。
- Constraint Respect 100%。
- confirmed workflow 人工抽样精度不低于 80%。
- graph Top-3 增益大于 0，且 over-expansion 不增加。
- 2,000 Skill 热路由 p95 不高于 100 ms。
- JSON stdout 恰好一个合法 envelope。
- Candidate Leakage、lift cap violation、source mutation、用户文件误删、Hook/Python 不一致均为 0。

held-out 与真实 workload 必须分别达标。每个主要 Package 建议至少 30 条，多 Skill 至少 80 条，高风险至少 50 条；要可信地判断 1% false activation，建议准备至少约 400 条负例。标签不足 300 条时禁止引入 learning-to-rank。

## 7. 当前自动化状态

本轮已经把 P0 风险加入自动回归：ambiguity 标签不再进入预测、图增益不再硬编码、payload 使用真实候选、LLMWiki enrichment 不可确认依赖、candidate edge 不影响默认路由、schema 0.1 兼容、profile/compatibility 门控、多 Skill 环与九步计划、Hook route-only，以及 2,000 Skill p95。

合成 agent-first 门禁集上，hybrid 当前达到 P@1 100%、Recall@3 100%、False Activation 0%、High-risk Review 100%、Constraint Respect 100%。这只是合成下限，不足以把默认策略从 legacy 切为 hybrid；切换仍取决于独立真实 workload。Graph 暂无已证明的正增益，因此继续保持 shadow。

## 8. 执行方式

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m obsidian_skill_router scan --skills-root <fixture-root> --vault <test-vault>
python -m obsidian_skill_router validate --vault <test-vault> --strict --check-sources
python -m obsidian_skill_router eval --vault <test-vault> --cases <cases.json> --strategy legacy --strategy hybrid --strategy hybrid-graph
```

所有生成 Vault、性能样本、真实 smoke scan 和评测报告只能写入 `test_artifacts/`。
