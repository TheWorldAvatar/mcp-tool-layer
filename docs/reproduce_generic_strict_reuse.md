# 复现：泛规则 no-prompt / 泛规则 strict（带 reuse）

给本仓库（v2）用。对照的是官方 **Pipeline no-contract** × **OX 泛规则** 这对实验，不是官方 30 篇的「官方 no-prompt」（TBox + 领域造句、不含 7 条 generic）。

新生成 MCP、当场重抽都可以。必须对齐的是 **KG 这一步模型看见的信息** 和 **MCP ReAct 开关**。

当前代码用 **`--protocol generic-strict` / `generic-noprompt` 一把锁死 1:1**（seed / 模型 / 信封 / 防火墙 / 无正文 / 无 ONEPASS / 无 `--from-main-run`）。第 2 节是验收清单，不要再手拼。`20260907_123728_e2e1` 里**旧的** main KG（灌正文 / `KG_BINDING`）不是这场实验；该 hash 后来已按 `--protocol` 重跑。不要用 `ox_e2e1b` 的分数。

不要改对照仓库  
`C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction`  
的源码、官方 score pack、冻结抽取。打分只只读调用它的 scorer。

---

## 1. 这场实验是什么

同一份抽取 ledger，两套构图：

| 角色 | 名字 | 模型看见什么 |
|---|---|---|
| Pipeline | **官方 no-contract** | user：DOI / 根 label / 根 IRI / SEMANTIC_HINTS。造图规则只在 MCP `instruction` + `create_*` docstring。**无论文正文。** |
| OX A | **泛规则 no-prompt** | system：7 条 generic + `FROM_EXTRACTION`（无正文）+ `FULL_HINTS` + **`ENTITY_REUSE_CONTRACT`** + TBox 评注。人消息：full_hints + 根绑定 + **reuse inventory**。 |
| OX B | **泛规则 strict** | system：7 条 generic + occurrence / ownership 地图。**无 TBox handbook、无 `EXTENSION_CONTRACT`、无 ONEPASS 合同。** 人消息：full_hints + 根绑定 + **reuse inventory**。 |

Reuse 指 OX 的 `SAME_PAPER_REUSABLE_ENTITIES` / `CROSS_DOCUMENT_REUSABLE_ENTITIES`，外加 parse 后 `canonicalize_reused`。不是 `--from-main-run`（那是继承 Pipeline TTL，是另一场实验）。

Pipeline 没有这两块 inventory。它的 reuse 只来自 MCP 工具（`check_existing_*` / reusable 类政策）。不要把 OX inventory 塞进 Pipeline user。

不要开 `--official-onepass-guidance`。不要把 `KG_BUILDING_*.md` 灌进 Pipeline。

---

## 2. 锁进 `--protocol` 的验收清单

### Pipeline KG

文件：`src/kg_building/pipeline/binding.py`、`main_kg/run.py`、`main_kg/agent.py`、`src/extraction_runtime/agent/client.py`。

1. **删掉正文。** `run.py` 不要 `load_paper_content` 再传给 KG。`bind_kg_runtime_context` 禁止拼 `PIPELINE-INJECTED SOURCE TEXT`。抽取仍可读 `{hash}_slim.md`；构图 agent 不能读。
2. **换成官方 no-contract 信封。** 删掉 `KG_BINDING` 六条（尤其是 “hints and paper text”）。user 只许是：

```text
Use the attached MCP for the bound graph-building task.

Runtime bindings:
- DOI: {doi}
- Bound root label: {entity_label}
- Bound root IRI: {entity_uri}

Semantic ledger supplied to the agent:
{iteration_hints}

Before export_memory, call every public create_* that matches a ledger heading. Do not export while any of those tools is still unused.
```

`{iteration_hints}` = 该实体 iter2 + iter3 + iter4，一次 session。不要 iter1。

3. **`mcp_instruction_in_user=False`。** MCP `instruction` 留在 system（`create_react_agent` 的 prompt）。官方 no-contract 没有把 instruction 再拷进 user。
4. **打开防火墙和 history projection。** `run_kg_agent` → `agent.run(..., react_history_projection=True, react_argument_firewall=True)` 已经在传。v2 `loop.py` 里这两个函数仍是空钩子（`return state`），旗标亮着但行为不等于官方。原本代码、插入点和用法见 `docs/react_firewall_and_history_projection.md`。
5. **环境变量**（进程和 MCP child 都要有）：

```text
TWA_LLM_SEED=42
TWA_MCP_TOOL_DESCRIPTIONS_ENABLED=0
TWA_SEMANTIC_OPERATION_SURFACE=1
TWA_GENERATED_ARTIFACT_ROOT=<本趟 generated/runs/<id>>
```

6. 无 judge / 无 hint revision / 无 posthoc。不要再引入这些循环。
7. 把实际发给模型的 user / system / MCP instruction 写到  
   `runtime/<hash>/prompts/kg_building/<entity>.user.md`（以及 instruction 副本）。没有落盘，后面无法验收。

允许留下、且不算造图合同：根 IRI、顶实体 identity manifest、`init_memory`/`export_memory` 生命周期。官方 no-contract 也有这些绑定。

### OX

文件：`src/kg_building/ontologx/cli.py`、`run_loop.py`。`prompt_builder.py` 和 `graph_merge.scoped_reuse_inventory` 已经有合同和 inventory，**主循环没用上**。

1. CLI 增加 `--prompt-profile {generic-noprompt, generic-strict}`。默认不要偷偷等于其中之一而不写进 `summary.json`。
2. **禁止 `--official-onepass-guidance`。** `build_system_prompt(..., official_onepass_guidance=False)`。
3. **generic-noprompt** 用 `build_system_prompt(..., from_extraction=True, full_hints=True, entity_reuse=True, official_onepass_guidance=False)`。  
   **generic-strict** 用 `build_strict_noprompt_system_prompt()` / `_extension_prompt()`。不要走 `EXTENSION_CONTRACT`。
4. `parser.parse(..., extra_human=...)` 必须带：

```text
Target ChemicalSynthesis ... URI: <bound root>
<identity block if any>

Reusable entity inventories (reuse only when actually used):
SAME_PAPER_REUSABLE_ENTITIES
...
CROSS_DOCUMENT_REUSABLE_ENTITIES
...
```

用已有的 `entity_human_suffix` + `scoped_reuse_inventory(paper_graph, central_graph)`。第一篇 / 第一个实体 inventory 可以是 `(none yet)`，这是正常的。

5. 解析后按官方：`seed_reusable` + `canonicalize_reused`。论文内 merge 用 `reuse="paper"`。不要 `--no-entity-reuse`。
6. **不要** `--from-main-run`。OX 自己物化 main。`--hint-runs` 指向本趟 Pipeline run（只要它的 `mcp_run` ledger）。
7. **OX 用 Pipeline 对等 token budget。** Pipeline 在 `runtime/<hash>/responses/main_kg_building/{scope}.trace.json` 写下本次 one-shot KG 花费（v2 的 iter2+3+4 是一次 session，不要再和官方 `iter2/3/4_kg_building` 痕迹相加）。OX `parser.parse(..., token_budget=entity.token_budget)`；没有痕迹就拒绝无上限跑。官方分 iter 痕迹仍可回退。
8. 人消息标签可以仍是 `Paper:`，内容必须是 ledger，不是正文。系统提示必须有 `There is no paper body`。
9. 落盘 `out-dir/system_prompt.md`、每个实体的 human 消息、`summary.json`（写明 `prompt_profile`、`entity_reuse: true`、`from_main_run: null`、`official_onepass_guidance: false`）。

### 不要做的事

- 不要把 `KG_BINDING` 或 slim/stitch 正文送给 KG agent。
- 不要用 `ox_e2e1b` 的分数当基线。
- 不要改对照仓库的 scorer / 官方 pack。
- 不要把 7 条 generic 写进 Pipeline user（那是 OX 的；Pipeline 的等价物在工具描述里）。

---

## 3. 环境

| 项 | 值 |
|---|---|
| 工作目录 | 本仓库根 `D:\MCP-enhanced-MOPs-Extraction_clean-v2` |
| Pipeline Python | `C:\Users\xz378\AppData\Local\anaconda3\envs\mcp_layer\python.exe` |
| OX Python | `C:\Program Files\Python313\python.exe` |
| `PYTHONPATH` | 本仓库根 |
| 抽取模型 | `configs/extraction_models.json`（基本 gpt-4.1） |
| KG / OX 模型 | `openai/gpt-4o-2024-11-20`，temperature 0，seed 42 |
| Scorer | 对照仓库只读；`--pred-root` / `--out-root` 都在本仓库 run 目录下 |

打分（对照仓库内跑，写出本仓库）：

- chemicals / characterisation / CBU：`--full`
- steps：`--full --skip-order --ignore --no-vessel --llm-synonyms --llm-synonym-model openai/gpt-5.6-sol`

---

## 4. 怎么跑

一个 `--protocol` 就是整套 1:1 设置，不要再手拼 seed / 模型 / 信封 / 防火墙。

```powershell
# Pipeline：官方 no-contract KG（两条 OX profile 共用这一套）
python -m src.extraction_runtime ontosynthesis --protocol generic-strict `
  --generation-run <GEN_ID> --hash <hash> --tag gs

# OX A
& "C:\Program Files\Python313\python.exe" -m src.kg_building.ontologx `
  --protocol generic-noprompt `
  --hint-runs scenarios/mops/runs/<PIPE_RUN> --hash <hash> `
  --out-dir scenarios/mops/runs/ox_<tag>_generic_noprompt_reuse --score

# OX B（同一 hint-runs，只换 --protocol）
& "C:\Program Files\Python313\python.exe" -m src.kg_building.ontologx `
  --protocol generic-strict `
  --hint-runs scenarios/mops/runs/<PIPE_RUN> --hash <hash> `
  --out-dir scenarios/mops/runs/ox_<tag>_generic_strict_reuse --score
```

`--protocol generic-strict` 和 `generic-noprompt` 对 **Pipeline 是同一套 no-contract**；差别只在 OX 的 system prompt。不要 `--from-main-run`、不要 `--official-onepass-guidance`。steps 对照不要加 `--extension`。OX steps 出现 0 分、产物名捆在一起时，先看 `docs/ox_merge_and_identity.md`（同篇 IRI 碰撞、跨篇 central CCDC、publish 被官方 merge 并错），不要当空图。

抽取可以读 slim / conversion。`--protocol` 会把步骤锁到 `main_kg_building`（含抽取 + main KG，不含 extension）。全程 `TWA_GENERATED_ARTIFACT_ROOT` 钉死本趟 generated 包。

### 4.1 生成 MCP（可以新生成）

记下一趟 `generated/runs/<GEN_ID>`。不要混用对照仓库的 indep10。

### 4.2 抽取-only（可选）

若只要 ledger、先不构图，不要传 `--protocol`，用 `--until main_ontology_extractions`。

### 4.3 Pipeline KG（no-contract）

验收：`runtime/<hash>/prompts/kg_building/*.user.md`

- 有 DOI、根 IRI、hints
- **没有** `PIPELINE-INJECTED SOURCE TEXT`、没有 `KG_BINDING`、没有 `KG_BUILDING_ITER_`
- 没有论文段落（摘要 / Experimental Section 原文）

跑完后不要改 `mcp_run`。

### 4.4–4.5 OX

见本节开头两条 `--protocol` 命令。`--prompt-profile` 仍可用，但是 `--protocol` 的别名。

---

## 5. 怎么验收（先信息面，后分数）

把实际 prompt 当断言对象。分数不能证明协议对了。

### 5.1 Pipeline user（必须全过）

| 检查 | 过 | 不过 |
|---|---|---|
| 有 DOI / bound root label / bound root IRI | 有 | 缺根 IRI |
| 有 iter2+3+4 ledger | 有 | 只有一层，或混进 iter1 |
| `PIPELINE-INJECTED SOURCE TEXT` / slim / stitch 原文 | **无** | 有一篇正文 |
| `KG_BINDING` / “paper text as evidence” | **无** | 有 |
| `KG_BUILDING_ITER_` / ONEPASS 合同 | **无** | 有 |
| 7 条 generic（Emit exactly one ontosyn:ChemicalSynthesis…） | **无**（在 MCP 工具侧） | 写进了 user |
| `mcp_instruction_in_user` | false | instruction 被拼进 user |
| `react_argument_firewall` / `react_history_projection` | 都是 true | 默认 false |
| `TWA_MCP_TOOL_DESCRIPTIONS_ENABLED` | `0` | 未设或 `1` |
| `TWA_LLM_SEED` | `42` | 未设 |

### 5.2 OX system（按 profile）

**两条都要有**

- `# Generic OntoLogX graph rules`（7 条）
- `There is no paper body`
- 无 `official_onepass_ox.contract.md`
- 无论文正文

**generic-noprompt 还要有**

- `# Authoritative OntoSynthesis T-Box`（`ontosynthesis_parsed.md`）
- `# Task: KG materialization, not paper extraction`
- `# Whole-graph` / ITER2–4 互补（`FULL_HINTS_CONTRACT`）
- `# Entity reuse` + `SAME_PAPER_REUSABLE_ENTITIES` 字样（合同）

**generic-strict 还要有**

- `# Occurrence protocol` + `# Ownership and attachment`
- **没有** TBox 长评注、没有 `EXTENSION_CONTRACT`、没有 “One Add owns exactly one hasAddedChemicalInput” 这类领域造句（那些属于 no-prompt 合同，不属于 strict）

### 5.3 OX human（reuse，两条都要）

每个实体的 human 必须含：

```text
SAME_PAPER_REUSABLE_ENTITIES
CROSS_DOCUMENT_REUSABLE_ENTITIES
```

第一个实体可以是 `(none yet)`。同一论文第二个实体之后，`SAME_PAPER_*` 应出现上一实体用过的可复用 id（Document / Equipment / Supplier / VesselEnvironment / …）。  
`summary.json`：`entity_reuse: true`，`from_main_run: null`。

禁止：人消息里出现 markdown 论文、`KG_BINDING`、MCP `create_*` 编排。

### 5.4 运行时卫生

- `runtime/` 里只能有本次 `--hash`。出现别的 DOI hash（例如 e2e1 的 `bb5d60c7`）= MCP 写错目录，整趟作废。
- OX 两个 profile 的 hints SHA256 与 Pipeline `mcp_run` 一致。
- 打分 `--pred-root` / `--out-root` 在本仓库该 `out-dir` 下，不写对照仓库 `evaluation/data/`。

### 5.5 分数怎么读

同一抽取、同一 scorer 时，才比较：

`Pipeline no-contract` vs `OX 泛规则 no-prompt + reuse` vs `OX 泛规则 strict + reuse`

不要和对照仓库 0827/0903 官方 30 篇表比绝对值：抽取和 MCP 包都是新的。  
`ox_e2e1b`（无 reuse extra_human、Pipeline 带正文）不能当对照。

---

## 6. 一趟最小验收（建议 0c57bac8）

1. 改完第 2 节。
2. 生成 MCP → 抽 0c57bac8 → 只跑 main KG（no-contract）。
3. 打开 `prompts/kg_building/UMC-1.user.md`：无正文、无 `KG_BINDING`。
4. 同一 `hint-runs` 跑 generic-noprompt 和 generic-strict。
5. 打开两个 `system_prompt.md`：按 5.2 勾。
6. 打开 UMC-2 的 human：`SAME_PAPER_REUSABLE_ENTITIES` 不应仍是空（UMC-1 已跑过）。
7. 只读 scorer 打 steps；报告里写清 GEN_ID、抽取 run、两个 OX `out-dir`。

三条都过 5.1–5.4，才算复现了这场实验。分数是结果，不是协议证明。
