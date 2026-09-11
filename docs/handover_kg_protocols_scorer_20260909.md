# Handover：KG 四档协议、实验结果、Scorer（2026-09-09）

给下一个 session 用。数字以各 run 的 `scores/scoring_steps/_overall.md` 为准；本文是当时锁定对照，不是自动刷新的仪表盘。

更细的四档 prompt 对照（含 UMC-1 Add 例子）：[kg_four_prompt_surfaces.md](kg_four_prompt_surfaces.md)。工作仓也有一份同名文件。

---

## 0. 仓库、解释器、钉死项

| 角色 | 路径 |
|---|---|
| **Repo A（打分 / 转换 / 不要当工作仓改 pipeline）** | `C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction` |
| **Repo B（工作仓：抽取、KG、campaign）** | `D:\MCP-enhanced-MOPs-Extraction_clean-v2` |
| Pipeline / 抽取 / 打分 Python | `C:\Users\xz378\AppData\Local\anaconda3\envs\mcp_layer\python.exe` |
| OX CLI Python | `C:\Program Files\Python313\python.exe` |
| Scorer 默认 | Repo A；封装 `Repo B/src/kg_building/ontologx/score_four.py` |
| GT | Repo A `full_ground_truth/`（`--full`） |

**模型钉死（除非实验另写）**

- 生成默认：`gpt-5-2025-08-07`
- 化学抽取：`gpt-4.1-2025-04-14`
- 医学抽取：`gpt-5-2025-08-07`
- KG / OX 默认：`openai/gpt-4o-2024-11-20`，temperature 0，**seed 42**
- steps 同义词 LLM：`openai/gpt-5.6-sol`（见 `score_four.SCORE_MODULES`）

**不要动**

- `generated/current.json`（仍指向 `20260907_211632_slimlock_ext` 一类冻结生成包）
- 002 分目录、官方 30 篇冻结 shard
- 已锁定的 `s1x30` / `s1p30` / `s1nv2*` / `kim*` / `s1wp*` 等 **overwrite**

**Windows：** extract/KG 常在 `[OK] Pipeline completed` 之后 `rc=3221225477`。只要 `.main_kg_building_done`（或 OX 的 ttl/summary）在，当成功。

**Pipeline `--config` + `--resume`：不要再加 CLI `--protocol`。** 协议写进 JSON：`experiment_protocol` + `pipeline_kg: "no-contract"`。CLI `--protocol` 会 `apply_to_pipeline_config` 并回写 config。OX **必须** `--protocol`。

**Campaign 标签陷阱**

| 标签形态 | 含义 |
|---|---|
| `s1p30` / `s2p30` | Pipeline **generic-strict** 30 篇 |
| `s1x30` / `s2x30` | **OX** generic-strict 30 篇 |
| `s1xs30` | Pipeline **extension** generic-strict（steps F1 可与 `s1p30` 逐篇相同，kind 是 extension） |
| `s1xn30` | Pipeline **extension** generic-noprompt。**不是 OX** |
| `nv2` | generic-noprompt **v2**（现档菜谱已删 Crystallize） |
| `npx` / `s1npx30` | noprompt **v1 OX**；s1 塌成 0.640 的那次 **作废** |

不要把 `s1xn30` 叫 OX。不要把现仓 `--protocol full-prompt` 叫 Official（见 §2.5）。

---

## 1. 实验结果：位置、配置、headline F1

根目录（除非另写）：`D:\MCP-enhanced-MOPs-Extraction_clean-v2\scenarios\mops\runs\`

每个打分 run 看：

- `scores/scoring_steps/_overall.md` — 论文级 micro F1（headline）
- `merged/<hash>/steps.json` — 转换后预测
- Pipeline KG：`2026…_<tag>/runtime/<hash>/`
- OX 图：`ox_<tag>/` 或合并 `ox_*_30/<hash>/<hash>.ttl`；`summary.json` 含 SHACL / `stop_reason`

steps 打分旗标（与官方 30 篇对齐）：`--full --skip-order --ignore --no-vessel --llm-synonyms --llm-synonym-model openai/gpt-5.6-sol`。

抽取 ledger 与 KG 是 **indep10 / occurrence surface**；人话是 ITER2/3/4 并集，不是 PDF。

### 1.1 gpt-4o KG · generic-strict · 30 篇（锁定对照）

配置：`--protocol generic-strict`；KG `openai/gpt-4o-2024-11-20` seed 42；occurrence-only，无 T-Box。

| Pack | Pipeline F1 | Pipeline 分目录 | OX F1 | OX 分目录 | gap |
|---|---:|---|---:|---|---:|
| s1 | **0.847** | `20260908_220850_s1p30` | **0.740** | `20260908_223401_s1x30` | 0.107 |
| s2 | **0.838** | `20260908_221215_s2p30` | **0.688** | `20260908_223652_s2x30` | 0.150 |
| s3 | **0.836** | `20260908_232520_s3p30` | **0.717** | `20260908_232536_s3x30` | 0.119 |

### 1.2 gpt-4o KG · generic-noprompt v2 · 30 篇（现档菜谱）

配置：`--protocol generic-noprompt`。s1 v1 OX `s1npx30` 0.640 **void**。Manifest：`generated/campaigns/s23_generic_noprompt_v2_manifest.json`。

| Pack | Pipeline F1 | Pipeline 分目录 | OX F1 | OX 分目录 | gap | 检验 |
|---|---:|---|---:|---|---:|---|
| s1 | **0.858** | `20260909_114425_s1nv230` | **0.775** | `20260909_114653_s1nv2x30` | 0.083 | 先前已显著 |
| s2 | **0.853** | `20260909_160339_s2nv230` | **0.724** | `20260909_160544_s2nv2x30` | 0.129 | t p=7.4e-4，Wilcoxon p=3.2e-5，boot CI [0.082, 0.235]，wins 25/2/3 |
| s3 | **0.840** | `20260909_160900_s3nv230` | **0.729** | `20260909_161102_s3nv2x30` | 0.111 | t p=9.4e-4，Wilcoxon p=2.4e-5，boot CI [0.089, 0.264]，wins 26/1/3 |

s2/s3 Pipeline 分组 KG 示例：`20260909_151236_s2nv2e` 等；OX 合并图：`ox_s2nv2_30/`、`ox_s3nv2_30/`。

**不要**用 noprompt v1：`20260908_234654_s1np30`、`20260908_234904_s1npx30`、`s2np30` / `s2npx30`。

### 1.3 kimi-k3-low（抽取 + KG 都是 kimi）

| 协议 | Pipeline | OX | 读法 |
|---|---|---|---|
| generic-strict | 0.858 `20260909_001520_kimsp30` | 0.822 `20260909_141443_kimsx30` | t p=0.61；Wilcoxon p≈0.018；boot CI 含 0 |
| generic-noprompt | 0.870 `20260909_141745_kimnp30` | 0.872 `20260909_142121_kimnx30` | 打平 |

结论：OX 机制没废；gpt-4o 在 occurrence-only 上容易丢 nested hop，kimi 更能扛。

### 1.4 杂交：kimi 抽取 + gpt-4o KG（官方 5 篇）

| | Pipeline | OX |
|---|---:|---:|
| generic-strict | 0.834 `20260909_151743_k4s5sc` | 0.694 `20260909_151811_ox_k4s5sc` |
| generic-noprompt | 0.833 `20260909_151914_k4n5sc` | 0.655 `20260909_151951_ox_k4n5sc` |

gpt-4o builder 仍输。标签 `k4s5` / `ox_k4s5` 等。

### 1.5 with-prompt（gpt-4o）

| 集合 | Pipeline | OX | 备注 |
|---|---:|---:|---|
| 官方 5 | 0.800 `20260909_132245_s1wp5sc`（KG `20260909_125357_s1wp5`） | **0.582** `20260909_132321_ox_s1wp5sc`（图 `ox_s1wp5`） | OX **污染**：泄漏 “REQUIRED: every Add…” + with-prompt 附录。**禁止**与 25 篇合池 |
| 干净 25 | **0.876** `20260909_152025_s1wp25sc` | **0.745** `20260909_152150_ox_s1wp25sc` | 冻结 `ontosyn_with_prompt_guidance.md` |
| 用户要求的 pooled 30 | 0.856 | 0.703 | gap 0.153；t p=3.2e-5。干净 25 仍显著。官方 5 Wilcoxon p=0.0625（n=5 地板） |

### 1.6 full-prompt / Official

| 项 | 状态 |
|---|---|
| `--protocol full-prompt`（`s1fp5`） | **杀掉**。实现是 `official_onepass_guidance=True`（ONEPASS 合同 **替换** FULL_HINTS），id 应叫 `official_onepass_guided_*`，不是 Official。未完成目录 `20260909_153141_s1fp5` 不要当 Official |
| Official OX 冻结 | Repo A `baselines/ontologx_ontosyn/runs/off30_0827_indep10_gpt4o_ox/s00_7ba809dd/`。`full_hints=true`，`official_onepass_guidance=false`，无 occurrence map，无 7 条 generic rules |
| Official Pipeline 冻结 | `scenarios/mops/runs/off30_0827_indep10_gpt4o_pipe/pipeline.occurrence.json`。薄 envelope；菜谱在 MCP pack `ai_generated_contents_occurrence_surface_20260902_indep10` |
| 现仓 `build_system_prompt(..., official_onepass_guidance=False)` | = Generic NP 栈（Official 长串 **加上** 7 条 generic rules），**不是**冻结 Official |

Official 30 篇要等用户指定跑哪套 Official 5 再扩。

### 1.7 其它不要引用的标签

- `s1xn30` / `s2xn30` / `s3xn30`：Pipeline **extension** noprompt，不是 OX
- `s1xs30`：Pipeline extension strict
- e2e002 / e2e003 全家：更早端到端，不是这轮 1:1 协议锁定表

Canvas（可选打开）：`canvases/s2-s3-generic-noprompt-v2-significance.canvas.tsx`、`canvases/with-prompt-30-case.canvas.tsx`、`canvases/ox-graph-vs-scoring-mechanism.canvas.tsx`。

---

## 2. 四种 mode：含义、配置、怎么启动

代码钉死：`Repo B/src/kg_building/experiment_protocol.py`

```text
PROTOCOL_NAMES = ("generic-noprompt", "generic-strict", "with-prompt", "full-prompt")
```

四档都是 **构图**（已有 ledger，不读论文）。人话都是 `SEMANTIC_HINTS_V1` ITER2/3/4。差的是 system / 附录 / MCP 说明。Pipeline 走 `create_*`；OX 走 `SynthesisGraph`。

公共锁：`pipeline_kg: no-contract`；抽取 revision **开**；KG revision **关**；entity reuse；无 `--from-main-run`。

### 2.1 generic-strict（口令里的 strict）

**含义：** 只教 occurrence / 所有权。无 T-Box，无 Graph-rules 菜谱，无 with-prompt guidance，无 ONEPASS。

- Pipeline：薄 envelope（DOI / bound root / ledger）。构图在 MCP `create_*` 参数（如 `hasAddedChemicalInput_label`）。
- OX：Occurrence protocol + Ownership map + 7 条 `# Generic OntoLogX graph rules`。
- **嵌套 hop**（`hasAddedChemicalInput`）不是 Add 的 self-facet。gpt-4o OX 常见：Add 只有 type/label/order，边丢了；type-only 仍高，attribute F1 塌。

### 2.2 generic-noprompt（口令里的 no prompt）

**含义：** 名字误导。相对的是「没有 with-prompt 那份 md、没有 ONEPASS」，**不是**没有构图说明。

= generic-strict 底座 **+** 长 Graph rules 菜谱 **+** 整份 `ontosynthesis_parsed.md`。

现档菜谱已删 Crystallize（「There is no ontosyn:Crystallize」）。Official 冻结菜谱仍列出 Crystallize。

### 2.3 with-prompt

**含义：** 仍站在 generic-strict occurrence 上，附录冻结 `src/kg_building/resources/ontosyn_with_prompt_guidance.md`。**不贴 T-Box。** 明文要求 OX 发出 Add + ChemicalInput + `hasAddedChemicalInput` 边。

不要把 Add-edge 教学写进 **共享** generic-strict ownership map 或共享 SHACL SPARQL。

### 2.4 full-prompt（代码里的第四档）

**现实现：** `official_onepass_guidance=True`：from_extraction + full_hints 表面上再叠 **ONEPASS 合同**（替换 FULL_HINTS 合同）+ T-Box。这是 `official_onepass_guided_*`，**不是** Official 冻结件。

`experiment_protocol.py` / CLI help 若仍写 “historical official system”，以冻结 shard 和 [kg_four_prompt_surfaces.md](kg_four_prompt_surfaces.md) §7 为准，以代码 `full_prompt.py` 为准：它加载的是 `official_onepass_ox.contract.md`。

### 2.5 Official 不是这四档之一

Official OX：`--full-hints`，**不开** `--official-onepass-guidance`，默认 `ontosynthesis` profile，无 occurrence map。要用冻结 `system_prompt.md`，不要用 20260904 之后已插入 generic rules 的现仓 `OUTPUT_CONTRACT`。

Official Pipeline：薄 prompt + 厚 MCP docstring，`kg_full_hints_onepass=true`。

### 2.6 怎么启动

工作目录：Repo B。打分 `--score` 写进 **该 run** 的 `merged/` `scores/`，scorer 源码在 Repo A（只读）。

**OX（必须 `--protocol`）**

```powershell
cd D:\MCP-enhanced-MOPs-Extraction_clean-v2
& "C:\Program Files\Python313\python.exe" -m src.kg_building.ontologx `
  --protocol generic-strict `
  --hint-runs scenarios/mops/runs/<PIPELINE_EXTRACT_OR_KG_RUN> `
  --hash 0c57bac8 `
  --out-dir scenarios/mops/runs/ox_<tag> `
  --score
```

`--hint-runs` 指向带 ITER2/3/4 ledger 的 Pipeline run。可多次 `--hint-runs`。换协议：`generic-noprompt` | `with-prompt` | `full-prompt`（后者勿当 Official）。

**Pipeline 新跑（mint，CLI 可以带 `--protocol`）**

```powershell
& "C:\Users\xz378\AppData\Local\anaconda3\envs\mcp_layer\python.exe" `
  -m src.extraction_runtime ontosynthesis `
  --protocol generic-strict `
  --tag s1p30 --hash 0c57bac8 --test --score
```

`--protocol` 会锁 steps 到 `main_kg_building`、seed 42、gpt-4o KG、no-contract。

**Pipeline 续跑（JSON 里已有协议：不要再传 `--protocol`）**

```powershell
& "C:\Users\xz378\AppData\Local\anaconda3\envs\mcp_layer\python.exe" `
  -m src.extraction_runtime ontosynthesis `
  --config scenarios/mops/runs/<PIPE_RUN>/pipeline.resolved.json `
  --resume --hash 0c57bac8 --test --score
```

JSON 需要：`"experiment_protocol": "generic-strict"`（或其它档）、`"pipeline_kg": "no-contract"`。

生成包：`--generation-run` / `--generated-root`；不要改 `generated/current.json`。

Campaign 脚本示例：`generated/campaigns/run_s23_generic_noprompt_v2.py`。

---

## 3. Scorer：现在怎么跑、发现了什么、下一步要做什么

### 3.1 运行链路（steps，当前代码）

1. **图 → JSON（Repo A 转换）**  
   Pipeline：`runtime/<hash>/ontosynthesis_output/*.ttl`  
   OX：`ox_*/<hash>/<hash>.ttl` 或 per-entity `cs1/cs1.ttl`  
   `scripts/merge_and_conversion_main.py` / `ontosynthesis_step_conversion.py` → `merged/<hash>/steps.json`。

2. **刚改过的转换字段（已进 Repo A，尚未用新规则重打锁定分）**  
   - `productNames`：优先 ChemicalOutput 标签（及 `hasAlternativeNames`）  
   - `synthesisLabel`：非哈希的 ChemicalSynthesis `rdfs:label`  
   - Pipeline 哈希文件名（`…--8c25330d4147`）不当产品名  

   **旧 merged JSON** 仍可能把 CS 标签塞进 `productNames[0]`。打分侧会再处理。

3. **配对（`evaluation/scoring_steps.py`）**  
   一篇论文多个 `Synthesis`，GT↔Pred 一对一（bitmask 全局分配）。CCDC 在 steps 上 **经常空**，不能当主锚。顺序：
   - 唯一 CCDC（两边都恰好一个）  
   - 唯一短码（`vmot-2`、括号里的 `1` 等）  
   - 产品名：精确重叠 / titled alias / **token Jaccard** / LLM `same_product`（gpt-4o，政策写「忽略 synthesis of」）  
   - 门槛：`identity_score[1] >= 450`（内部 2000×Jaccard）或 LLM 判同一产品  
   - 同产品多路线：`_synthesis_route_score`（via / from / precursor）再比 step 类型  

4. **步内打分**  
   `--skip-order`：按类型对齐，Add 再按药品名。字段级 TP/FP/FN。`--ignore --no-vessel`。同义词走 gpt-5.6-sol。

5. **输出**  
   `scores/scoring_steps/_overall.md`、`error_details/`、type-only 表。type-only 高而 micro 低 = 类型在、属性/边没转出来。GT only + Pred only = **实体没配上**，不是空图。

### 3.2 已确认的问题（不是「OX 没建图」）

审计对象：s2/s3 generic-noprompt v2，gpt-4o。图在 `ox_s2nv2_30` 等。

**A. 配对偏置（公平性，必须修）**

`3f239659`：OX 图有 5 Add、5 条 `hasAddedChemicalInput`、11 step。Pipeline F1 0.954，OX **0.000**。

原因：转换曾把 CS `rdfs:label` 放进 `productNames`；Pipeline 哈希名被丢掉，只剩 `[Cu12(TMTA)8(H2O)12] nanocage`，union Jaccard **625 ≥ 450**；OX 工序长句留在名单里，union Jaccard **444 < 450**，整篇当 Pred-only。GT CCDC `1439771` OX 常没写。

这是 **打分/转换把产品身份和路线句子塞进同一袋 token**，不是空图。s2 30 篇里这种「整篇零配对」主要就是这一篇；去掉工序句后能配上。用户指出：**用 regex 猜 OX 怎么写 from/via 不稳定**，不能当最终方案。

**B. 真建图（OX gpt-4o）**

- **裸 Add：** 药品挂在 CS `hasChemicalInput`，Add 没有 `hasAddedChemicalInput`。转换只读 hop → `addedChemical: []`。s2 全裸：`5175f0fe`、`aaf9ce20`；s3 全裸：`a527729b`、`f4f7330e`。另有约 8 篇部分 hop。SHACL AddShape `minCount 1` 会失败，但 `budget_exhausted` 后 **非法图仍进评分**（s2 约 28/87 entity）。  
- **截断：** `93aab3a3` 三个笼子有根，两个停在 ~6 节点 / 1 Add，`conforms_within_budget`。SHACL 不要求配方完整。  
- Pipeline `create_Add` 原子参数，抽到的样本里没有裸 Add。

**C. OX 并非全局更差**

s2 `a527729b` OX 0.894 > Pipeline 0.871；s3 `5175f0fe` hop 齐后打平 0.815；kimi noprompt 打平。

### 3.3 已经改了但不够（用户否决 regex）

Repo A 已落地：

- 转换：`synthesis_product_fields`（Output vs `synthesisLabel`）  
- 打分：`collect_product_and_route_names` 用 **from/via/by reacting/in DMF/…** 切工序尾巴再算 Jaccard  
- 单测：`tests/test_scoring_steps_synthesis_matching.py`、`tests/test_ttl_merge_hashed_synthesis_labels.py`  

**未**用新 scorer 重打锁定 30 篇（按用户要求先不 overwrite）。

用户明确：regex 猜 OX 命名不稳定，要更稳、更全。

### 3.4 正准备做（被 handover 打断，尚未写完）

不要再切英文工序句。改成 **结构 + 两两配对**：

1. **产品身份只用「一条条名字」的最佳配对**，不要把所有名字 token **并成一大袋**（并袋才会被长句稀释）。GT 的公式名 vs Pred 的 ChemicalOutput 短名可以过 450；同一 Pred 里的工序长句单独比、失败即可，不必猜怎么切。  
2. **路线身份用字段：** 有 `synthesisLabel` 就用它排序同产品多路线；没有则用完整 `productNames` 做 route 排序（只打同分产品候选），**不**用 from/via 正则当门。  
3. **哈希文件名**仍按 `…--[hex]` 丢掉（这是 Pipeline 导出结构，不是猜 OX 英语）。  
4. 转换保持：有 ChemicalOutput 就不要把 CS 标签写进 `productNames`。无 Output 时整段 CS 标签当 **一条** 名字交给两两配对，不再 regex 抽「产品头」。  
5. LLM `same_product` 继续看 **名字列表**（政策已要求忽略 “synthesis of”），作为 Jaccard 之后的网，不替代两两硬匹配。  
6. 配对改完后：**单测先绿**，再问用户要不要重打哪一场 30 篇。不要动 `current.json` / 002 / 旧分目录 overwrite。

实现落点：`scripts/output_conversion_ttl_to_json/name_utils.py`（删 `_ROUTE_TAIL`）、`evaluation/scoring_steps.py`（`_synthesis_identity_score` 改为 max pairwise Jaccard）。

### 3.5 重打分时

```powershell
# cwd = Repo A；pred 在 Repo B 的 merged/
# 或 Repo B: python -m src.kg_building.ontologx ... --score --scorer-repo <Repo A>
```

新配对会抬 `3f239659` 的 OX F1（图本身在），**不会**抹平裸 Add / 截断带来的 0.10+ gap。

---

## 4. 下一 session 建议顺序

1. 按 §3.4 改 scorer（去掉工序 regex，上 pairwise + `synthesisLabel`）。  
2. 跑 `pytest tests/test_scoring_steps_synthesis_matching.py tests/test_ttl_merge_hashed_synthesis_labels.py`。  
3. 问用户重打哪套：建议先 `s2nv230` vs `s2nv2x30` 验证 `3f239659`。  
4. Official 30 等用户选：冻结 Official 不对称表面，还是另做 1:1 Official 信息面。  
5. 不要把 Add-edge 教学塞回 generic-strict 共享 ownership map。
