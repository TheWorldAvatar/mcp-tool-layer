# 四档 KG prompt 对照：generic-strict / generic-noprompt / with-prompt / Official

本文说明 **构图阶段**（已有抽取 ledger，不再读论文正文）四档 system/user prompt 差在哪。贯穿例子用 `0c57bac8` / UMC-1 的一条真实 heading。

锁定 1:1 三档在工作仓 `D:\MCP-enhanced-MOPs-Extraction_clean-v2`（`--protocol generic-strict | generic-noprompt | with-prompt`）。**Official** 不是这三档里的任何一个，也不是后来的 `--official-onepass-guidance`。它是官方 30 篇套 `*.ox_official_noprompt` / 对应 Pipeline，冻结件见下文路径。

## 1. 先记住三件事

1. **人话输入几乎一样。** 四档都是 `SEMANTIC_HINTS_V1` 的 ITER2/3/4 并集，不是 PDF。差的是 **system / 附录 / MCP 工具说明** 教模型怎么把 ledger 变成图。
2. **Pipeline 和 OX 编码不同。** Pipeline 走 `create_*`；OX 走 `SynthesisGraph`（nodes + relationships）。同一档协议尽量给同一套语义，但字面 prompt 不会逐句相同。
3. **Official 的 Pipeline 和 Official 的 OX 本来就不是同一段 system 字。** 表里 Official Pipeline 把构图菜谱放在冻结 MCP 的 `create_*` docstring；Official OX 把长 T-Box + Graph rules 放进 system prompt。

## 2. 总览

| | generic-strict | generic-noprompt | with-prompt | Official OX | Official Pipeline |
|---|---|---|---|---|---|
| 实验身份 | 锁定 1:1 | 锁定 1:1 | 锁定 1:1 | 官方 30 篇 `*.ox_official_noprompt` | 官方 30 篇 indep10 |
| 主界面 | occurrence / 所有权 | 同上 + 菜谱 + T-Box | 同上 + 冻结构图 guidance | 长 T-Box 菜谱（无 occurrence map） | 薄 envelope；菜谱在 MCP |
| Occurrence / 所有权 map | OX：有；Pipeline：在工具参数里 | 同左 | 同左 | **无** | 在 `create_*` 参数里 |
| 7 条 `# Generic OntoLogX graph rules` | OX 有 | OX 有 | OX 有 | **无**（20260904 之后才进合同） | 无（工具签名承担） |
| 长 Graph rules 菜谱（洗=Filter 等） | **无** | **有**（现档已删 Crystallize） | 部分写进 with-prompt guidance | **有**（冻结原文仍列 Crystallize） | 在 MCP docstring，不在 KG user prompt |
| 整份 T-Box `ontosynthesis_parsed.md` | **无** | **有** | **无** | **有** | **无** |
| FROM_EXTRACTION / FULL_HINTS 合同 | 无独立合同（ledger 仍在） | 无独立合同 | guidance 里有「ITER2/3/4 是同一张图」 | **有**（FULL_HINTS，**不是** ONEPASS 合同） | 一次 session 吃完 ITER2/3/4；不贴 OX 合同 |
| `official_onepass_ox.contract.md` | 无 | 无 | 无 | **无** | 无 |
| 冻结 with-prompt guidance | 无 | 无 | **有** | 无 | 无 |

不要把 Official 叫成「没写 system prompt」，也不要把它和 `--official-onepass-guidance` 混为一谈。后者会用 ONEPASS 合同 **替换** FULL_HINTS 合同，打分 id 是 `official_onepass_guided_*`，不在 Official 表里。

## 3. 贯穿例子：同一条 ledger

抽取来自 `20260908_193928_s1a`，`0c57bac8` / UMC-1，`iter3_hints_UMC-1.txt`：

```text
Add
hasOrder: 1
hasAddedChemicalInput: Bis(cyclopentadienyl)zirconium dichloride
hasAlternativeNames: ... Zirconocene dichloride ... Cp2ZrCl2 ...
hasAmount: 17.5 mg, 0.06 mmol
hasChemicalDescription: metal precursor
hasChemicalFormula: C10H10Cl2Zr
```

正确图（所有四档都 **应该** 得到这个结构；教法不同）：

```text
ChemicalSynthesis (UMC-1)
  --hasSynthesisStep--> Add (hasOrder=1)
                          --hasAddedChemicalInput--> ChemicalInput
                               rdfs:label = Bis(cyclopentadienyl)zirconium dichloride
                               hasAmount = 17.5 mg, 0.06 mmol
                               hasChemicalFormula = C10H10Cl2Zr
                               ...
```

失败形态（generic-strict 上 gpt-4o OX 常见）：只造了 `Add` 的 type / label / order，**没有** `hasAddedChemicalInput` 边。type-only F1 仍可接近 1.0，attribute F1 会塌。

---

## 4. generic-strict

**一句话：** 只告诉模型「每个 heading 是一个 occurrence，字段按所有权表挂」；不教化学菜谱，不贴 T-Box。

### 4.1 Pipeline 实际吃到的 user prompt

薄 envelope，构图规则不在这段字里，而在 MCP 工具参数（`hasAddedChemicalInput_label` 等）：

```text
Use the attached MCP for the bound graph-building task.

Runtime bindings:
- DOI: 10.1021/acsami.7b18836
- Bound root label: UMC-1
- Bound root IRI: <这篇的 ChemicalSynthesis IRI>

Semantic ledger supplied to the agent:
SEMANTIC_HINTS_V1
... ITER2 / ITER3 / ITER4 ...
Add
hasOrder: 1
hasAddedChemicalInput: Bis(cyclopentadienyl)zirconium dichloride
...

Before export_memory, call every public create_* that matches a ledger heading. Do not export while any of those tools is still unused.
```

对应调用形态（信息在 schema，不在 system 散文）：

```text
create_Add(
  hasOrder=1,
  hasAddedChemicalInput_label="Bis(cyclopentadienyl)zirconium dichloride",
  hasAddedChemicalInput_hasAmount="17.5 mg, 0.06 mmol",
  hasAddedChemicalInput_hasChemicalFormula="C10H10Cl2Zr",
  ...
)
```

漏掉 `hasAddedChemicalInput_label` 就是一次不合格的工具调用，防火墙可以挡。

### 4.2 OX 实际吃到的 system prompt

三块，**没有** T-Box、**没有**「洗=Filter」长菜谱：

1. Role：ledger → `SynthesisGraph`，没有论文正文。
2. `# Generic OntoLogX graph rules`（7 条操作约束：单根、最具体类型、连通、空值不写、前缀……）。这是 **OX 接口说明书**，不是化学菜谱。
3. `# Occurrence protocol` + `# Ownership and attachment`。

Occurrence 在说什么（摘录）：

```text
Read each occurrence heading in the ledger exactly once and emit one node
of that heading's owner class.
Owner classes: Add, ChemicalInput, ChemicalOutput, Dry, Evaporate, ...
Headings of different owner classes remain distinct occurrences even when
their labels match.
Put every supported detail from that heading onto that same occurrence
through the ownership map below.
```

对上面那条 `Add` heading，所有权表把字段拆成两类：

| 写在 Add **自己**上（self facet） | 写在 **嵌套子节点**上 |
|---|---|
| `hasOrder`, `hasParameter`, `hasStepDuration`, `hasTargetPh`, `isLayered`, `isStirred` | `hasAddedChemicalInput` → 一个新的 ChemicalInput；amount / formula / names / purity 都在那个子节点上 |
| 父边：`ChemicalSynthesis --hasSynthesisStep--> Add` | 子边：`Add --hasAddedChemicalInput--> ChemicalInput` |

渲染出来大约是：

```text
## Add
Parent: attach this occurrence to the bound ChemicalSynthesis root via hasSynthesisStep.
Identity of this occurrence includes its hasOrder from the heading.
Facets on this occurrence: hasOrder, hasParameter, hasStepDuration, hasTargetPh, isLayered, isStirred.
Nested ownership:
- hasAddedChemicalInput: related node owned by this Add; properties on that node:
  hasAlternativeNames, hasAmount, hasChemicalDescription, hasChemicalFormula, hasPurity, ...
- hasVessel: ...
```

**关键：`hasAddedChemicalInput` 不是 Add 的 self facet。** 模型必须另造节点再连边。generic-strict **不会**再写一句「REQUIRED: every Add has exactly one hasAddedChemicalInput」。OX 若把 nested hop 当成「可省略的可选参数」，就会产出裸 Add。

对比：标题若是 `Filter` + 洗液，嵌套 hop 是 `hasWashingSolvent`，不是 `hasAddedChemicalInput`。同一种液体，owner class 不同，挂的边就不同——这就是所有权，不是化学常识。

还有一类 **synthesis-level** `ChemicalInput`（ITER2，`hasChemicalInput` 挂在配方根上），和 Add 底下那个 step-local input 是两个 occurrence。同名 DMF 可以同时存在。

### 4.3 这条 Add 在 generic-strict 里被「教」了什么

- 教了：造一个 Add，order=1；Cp2ZrCl2 及相关量/式应落在 nested ChemicalInput。
- 没教：T-Box 里 Add 的长注释（「A dissolved in X 必须拆成两个 Add」那种原子化细则）。
- 没教：洗=Filter、保温=HeatChill 的类型消歧（那是菜谱，在 noprompt / Official / with-prompt 里）。

---

## 5. generic-noprompt

**一句话：** generic-strict 的底座 **加上** 长 Graph rules 菜谱 **加上** 整份 T-Box handbook。

名字容易误导：这里的 no-prompt 相对的是「没有 with-prompt 那份 guidance、没有 ONEPASS 合同」，不是「没有构图说明」。

### 5.1 两边各加什么

- **OX：** 仍保留 occurrence map + 7 条 generic rules，然后追加 Graph rules 菜谱和 `data/ontologies/ontosynthesis_parsed.md`。
- **Pipeline：** 薄 envelope 不变，user 后面追加同一份 Graph rules + 同一份 T-Box。MCP 工具仍然在。

Pipeline 追加头：

```text
# Generic-noprompt graph rules
These construction rules are part of generic-noprompt only.
They are not present in generic-strict.
```

### 5.2 菜谱在教什么（和 occurrence map 不同）

occurrence map 说「字段挂在谁身上」。菜谱说「这个化学过程应该是哪个类」：

```text
- One Add owns exactly one ontosyn:hasAddedChemicalInput. A clause that names N materials is N Add nodes.
- A wash of retained solid is Filter with ontosyn:hasWashingSolvent, not Add.
- A heat-to-temperature-and-hold (including solvothermal 130 degC for 2 days) is HeatChill, not Stir.
- Passive duration hold that yields crystals is HeatChill. There is no ontosyn:Crystallize class.
```

对 UMC-1 那条 Add：菜谱把「One Add owns exactly one hasAddedChemicalInput」写成 **必须遵守的构图规则**，不再只是 ownership 表里的 nested hop。对 `HeatChill / 60 degC / 8 h / isSealed` 那一块，菜谱明确这就是 HeatChill，不要改成 Stir。

### 5.3 T-Box 在教什么

`ontosynthesis_parsed.md` 按类给出定义和原子化细则。Add 一节（Official OX 冻结 `system_prompt.md` 里同一份 handbook 的开头）例如：

```text
## Class: Add
A synthesis step that introduces exactly one explicitly named chemical material or component.
- Each Add links to exactly one fresh step-local ChemicalInput.
- If one grammatical clause explicitly names N distinct materials ... emit exactly N Add occurrences.
- 'A dissolved in X' requires Add(A) and Add(X) when both A and X are explicit.
```

generic-strict **没有** 这一段。generic-noprompt 和 Official OX **有**。

现档 generic-noprompt 菜谱已删除 `Crystallize`（「There is no ontosyn:Crystallize」）。Official 冻结 Graph rules 仍把 Crystallize 列在允许步骤里，同时又说晶体析出不要再额外 emit Crystallize。不要把两档菜谱当成字节相同。

---

## 6. with-prompt

**一句话：** 仍站在 generic-strict 的 occurrence 表面上，再 **附录** 一份冻结构图 guidance；**不贴 T-Box**。

文件：`src/kg_building/resources/ontosyn_with_prompt_guidance.md`。

OX = occurrence map + 该 md。Pipeline = 薄 envelope + 该 md。两边都写明构造子编码不同、图必须相同。

### 6.1 和 generic-strict 比，多了什么

guidance 用自然语言把「怎么画这张图」写死，并且 **点名 OX 最常见的失败**：

```text
### Add and hasAddedChemicalInput (do not skip this edge)
This is the most common constructor failure.

OntoLogX: emit (1) the Add node, (2) a distinct ChemicalInput node,
(3) relationship Add --hasAddedChemicalInput--> ChemicalInput, and
(4) ChemicalSynthesis --hasSynthesisStep--> Add.
An Add that has type, label, and order but no hasAddedChemicalInput
relationship is invalid.
```

对 UMC-1 那条 heading，with-prompt 等于对着 OX 说：四个元素一个都不能少。generic-strict 只在 ownership 表里写 nested hop，没有这段「不要跳过这条边」。

guidance 也收了部分菜谱（洗=Filter、保温=HeatChill、没有 Crystallize），以及 ITER2/3/4 是同一张图、每次整图重发。它 **不是** 整份 T-Box（没有 `parsed.md` 里按 class 展开的原子化长注释）。

### 6.2 和 Official 比，少了什么

- 没有整份 T-Box handbook。
- OX 主界面仍是 occurrence / 所有权，不是 Official 那种 Role + 长菜谱 + T-Box 当 **主 system**。
- 没有 Official 的 FROM_EXTRACTION / FULL_HINTS 合同原文（guidance 里有类似「union of iterations」的短句，但不是那两份合同）。

### 6.3 污染警告（只影响已跑的 OX 5 篇）

`ox_s1wp5` 的 system 曾叠过后来撤回的 “REQUIRED: every Add…” 泄漏句 **再加** with-prompt 附录。那 5 篇 OX 不能当干净 with-prompt。其余 25 篇 `ox_s1wp25*` 按冻结 guidance 跑。协议定义以 md 文件为准，不以那次 5-case dump 为准。

---

## 7. Official prompt

**一句话：** OX 的主 system 是迁 generic rules **之前** 封的「长 T-Box 构图菜谱 + FROM_EXTRACTION + FULL_HINTS」；Pipeline 的 KG user prompt 仍然很薄，菜谱在冻结 MCP docstring。两边都 **不开** `official_onepass_ox.contract.md`。

### 7.1 Official OX 怎么启动

冻结 shard：`baselines/ontologx_ontosyn/runs/off30_0827_indep10_gpt4o_ox/s00_7ba809dd/`。

`summary.json` 钉死：

- `--prompt-profile` 默认 `ontosynthesis`（没传）
- `--full-hints`
- `official_onepass_guidance: false`
- `entity_reuse: true`，`parser: ontologx-full-hints`，`match_kg_budget: true`
- `prompt_source`: `data/ontologies/ontosynthesis_parsed.md`
- 人话：Pipeline ITER2/3/4，不是论文

对应 `build_system_prompt(from_extraction=True, full_hints=True, entity_reuse=True, per_entity=True, official_onepass_guidance=False)` 在 **20260904 插入 generic rules 之前** 的 OUTPUT_CONTRACT。

### 7.2 Official OX system 从上到下

冻结 `system_prompt.md` 顺序：

1. **Role + output contract**：只许 `SynthesisGraph`；不许 MCP / ledger / 正文。
2. **长 Graph rules 菜谱**（允许步骤列表里仍有 Crystallize；「One Add owns exactly one hasAddedChemicalInput」写在菜谱里）。
3. **T-Box 头** + 后面整份 `parsed.md`（Add 的原子化长注释等）。
4. **FROM_EXTRACTION**：人话是 ledger；有 heading 的步不能丢；amount 不要拆丢。
5. **FULL_HINTS**：`# Whole-graph construction from all iteration hints` — ITER2/3/4 是同一张图的三个视角，每次整图重发。
6. **ENTITY_REUSE + PER_ENTITY**：一篇一个 bound root。

**没有：**

- `# Occurrence protocol` / `# Ownership and attachment`
- `# Generic OntoLogX graph rules`（那 7 条）
- `official_onepass_ox.contract.md`（那份会 **替换** 第 5 步的 FULL_HINTS 合同）

对 UMC-1 那条 Add，Official OX 同时用三种语言教同一件事：菜谱里的「One Add owns exactly one input」、T-Box 里 Add 的 cardinality/原子化、FROM_EXTRACTION 里「Chemical identity is hasAddedChemicalInput」。它 **不** 用 generic-strict 那种「self facet vs nested hop」表格。

### 7.3 Official Pipeline

冻结配置：`scenarios/mops/runs/off30_0827_indep10_gpt4o_pipe/pipeline.occurrence.json`。

- `kg_full_hints_onepass: true`（一次 session 吃完 ITER2/3/4，对应 OX `--full-hints`）
- `kg_semantic_surface_no_contract_experiment: true`（薄 prompt）
- `kg_generic_onepass_prompt_experiment: false`（不用 GENERIC_ONEPASS 厚 prompt）

KG user prompt 与 generic-strict Pipeline 同族：

```text
Use the attached MCP for the bound graph-building task.

Runtime bindings:
- DOI: ...
- Bound root label: ...
- Bound root IRI: ...

Semantic ledger supplied to the agent:
{iteration_hints}

Before export_memory, call every public create_* that matches a ledger heading. Do not export while any of those tools is still unused.
```

构图规则在冻结包 `ai_generated_contents_occurrence_surface_20260902_indep10` 的 `create_*` docstring（occurrence surface JSON 就是从这些工具说明编出来的），**不**把 T-Box 或 OX Graph rules 贴进 KG system/user。

因此：表里「Official」不是「两边都贴同一段长 prompt」。是「OX 吃长 handbook；Pipeline 吃薄 prompt + 厚 MCP」。

### 7.4 现仓 `build_system_prompt(..., official_onepass_guidance=False)` 还不是 Official

20260904 之后，Repo A/B 的 `OUTPUT_CONTRACT` 已插入 `# Generic OntoLogX graph rules`。现跑「默认 ontosynthesis + full-hints、不开 ONEPASS」得到的是表里的 **Generic NP**（Official 长串再加 7 条 OX 操作约束），不是冻结 Official。要复现 Official OX，应对冻结 `system_prompt.md`，或用迁规则之前的 OUTPUT_CONTRACT。

---

## 8. 同一条 Add，四档各「催」什么

| 档 | 模型看见的额外教学 | 对 UMC-1 Add 的直接后果 |
|---|---|---|
| generic-strict | OX：occurrence + nested hop 表；Pipeline：工具参数名 | 应该造 Add+ChemicalInput+边；OX 容易只造裸 Add |
| generic-noprompt | 上一项 + 「One Add owns exactly one input」菜谱 + T-Box Add 长注释 | 同一结构，但写成必须遵守的菜谱/schema |
| with-prompt | 上一项的 occurrence 底座 + 「不要跳过这条边」guidance（无 T-Box） | 明确列出 OX 必须 emit 的 4 个元素 |
| Official OX | 菜谱 + T-Box + FROM_EXTRACTION + FULL_HINTS；无 occurrence 表 | 用 handbook 教原子化和边，而不是用所有权表 |
| Official Pipeline | 薄 envelope；`create_Add(..., hasAddedChemicalInput_*)` 在 MCP | 和 generic-strict Pipeline 同族，工具说明来自 0902 indep10 冻结包 |

## 9. 不要和这四档搞混的东西

| 名字 | 实际是什么 |
|---|---|
| `--official-onepass-guidance` / `official_onepass_ox.contract.md` | Official 的 **对照消融**。`full_hints=true` 时用 ONEPASS 合同 **替换** FULL_HINTS 合同。打分另开 id。 |
| Repo B 曾加的 `full-prompt` | 按「from_extraction + full_hints + ONEPASS + T-Box」接的，等于上一行，**不是** Official。 |
| generic-noprompt 的「no-prompt」 | 没有 with-prompt guidance、没有 ONEPASS 合同；**有** 菜谱和 T-Box。 |
| Official 的「no-prompt」 | 没有 ONEPASS 合同；**有** 长 T-Box 菜谱。 |
| with-prompt | 不是 Official，也不是 T-Box dump。 |

## 10. 源文件

锁定 1:1（工作仓）：

- 协议：`src/kg_building/experiment_protocol.py`
- OX strict：`src/kg_building/ontologx/strict_noprompt.py`
- occurrence 表：`src/kg_building/ontologx/resources/pipeline_occurrence_surface_ox.json`
- noprompt 菜谱 + T-Box 加载：`src/kg_building/generic_noprompt_graph_rules.py`
- with-prompt：`src/kg_building/resources/ontosyn_with_prompt_guidance.md`
- Pipeline 薄 envelope：`src/kg_building/pipeline/binding.py`

Official 冻结（本 Reproduction 仓）：

- OX system：`baselines/ontologx_ontosyn/runs/off30_0827_indep10_gpt4o_ox/s00_7ba809dd/system_prompt.md`
- OX summary：同目录 `summary.json`
- Pipeline 配置：`scenarios/mops/runs/off30_0827_indep10_gpt4o_pipe/pipeline.occurrence.json`
- 启动说明：`docs/official_pipeline_kg_building.md`
- 例子 ledger：工作仓 `scenarios/mops/runs/20260908_193928_s1a/runtime/0c57bac8/mcp_run/iter3_hints_UMC-1.txt`
