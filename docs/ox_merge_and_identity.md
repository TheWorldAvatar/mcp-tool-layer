# OX：产物身份被搅 + convert merge 吃错文件

给本仓库（repo B / v2）用。对照仓库（repo A）的 scorer / convert **只读**，不要改：

`C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction`

2026-09-07 五篇 `ox_gs5_generic_strict_reuse` 上，Pipeline > OX 合理，但 OX steps 出现 `0.000`（`0c57bac8` / `1b9180ec`）。**图不是空的。** Scorer 把同一产物标成 `umc-1 (GT only)` vs `umc-1 (Pred only)`，细粒度 TP 全空。

这和 Pipeline 防火墙无关。OX 不走 ReAct。根因是：v2 把多实体图并到一起时撞了 IRI，再按官方 Pipeline 目录形状交给 repo A 的 merge，标签被并到同一个 `ChemicalOutput` 上。

---

## 1. 现象（用 0c57bac8 对）

| 文件 | 产物边 |
|---|---|
| `ox_gs5.../0c57bac8/0c57bac8.ttl`（论文级，OX 自己 export） | UMC-1 → `ChemicalOutput-1`；UMC-2 → `ChemicalOutput-1_2`。**是对的。** |
| `score_runtime/0c57bac8/ontosynthesis_output/UMC-1.ttl` | UMC-1 → `ChemicalOutput-1`（标签 UMC-1） |
| `score_runtime/0c57bac8/ontosynthesis_output/UMC-2.ttl` | UMC-2 → **同一个** `ChemicalOutput-1`（标签 UMC-2） |
| `merged/0c57bac8/0c57bac8.ttl`（repo A convert 输出） | 两个 Synthesis 都指向 `ChemicalOutput-1`，该节点同时有 `rdfs:label "UMC-1", "UMC-2"` |
| `merged/0c57bac8/steps.json` | 每个 Synthesis 的 `productNames` 都是 `["UMC-1","UMC-2"]`，`productCCDCNumber` 空 |

官方 `scoring_steps.py` 配对要 CCDC 或短名在**全篇唯一**。两边都不唯一 → F1=0。Pipeline 同篇能打 0.723，因为 MCP 铸的是两颗独立 IRI（`.../wOqsPG59TeqT2noW` 和 `.../GnhJ43vfRtKg31SX`）。

`7ba809dd` 把机制暴露得很干净：只有 **VMOP-13** 名字是独一份，type-only F1=1.000；11/12/14/15 全部 GT only / Pred only。`50307a45` 能到 0.647，是长标题还能当锚，不是 merge 没坏。

---

## 2. 三条错误（按修的顺序）

### A. 同篇第二实体把 `ChemicalOutput-1` 撞上第一实体

**插这里：** `src/kg_building/ontologx/graph_merge.py` → `identity_key`（约 173 行）和 `attach_subgraph`（约 488 行）。

`attach_subgraph(..., reuse="paper")` 的注释写的是：

> only document/global classes match, and colliding occurrence-local ids are remapped.

实现反了。occurrence-local 的 key 是：

```python
if reuse == "paper":
    if not is_cross_entity_reusable(node):
        return ("occ", local, node.id)   # ChemicalOutput-1 == ChemicalOutput-1
```

第二实体还叫 `ChemicalOutput-1`，`_match_existing` 当成同一个节点，边被接到第一篇产物上。Document / Equipment 该 reuse（`DOCUMENT_SCOPE_REUSE_LOCAL`），产物 / 投入 / 步骤不该。

`ttl_export.instance_iri(paper_hash, node_id)` 用 `node.id` 拼 IRI。id 一撞，TTL IRI 就撞。

**该怎么改：** `reuse="paper"` 时，`ChemicalOutput` / `ChemicalInput` / step 的 identity **不要**带可跨实体碰撞的 `node.id`。对不上就走后面已有的

```python
while new_id in merged_nodes:
    new_id = f"{node.id}_{suffix}"
```

论文级 `0c57bac8.ttl` 其实已经靠这条出路做出过 `ChemicalOutput-1_2`。坏的是**每个实体自己那份** `main` 图在 publish 之前就被 canonicalize 到共享 id 上了。

`reuse="layer"`（同一合成的 iter 叠加）可以继续用 `("co",)` 合并同一个产物。不要动 layer，只修 paper。

### B. 同一 `out-dir` 把上一篇的 MOP/CCDC 带进下一篇

**插这里：** `src/kg_building/ontologx/run_loop.py`

```text
central_graph = None                          # 约 287，整个 batch 一份
...
extra_human = reuse_extra_human(..., central_graph)   # 写进 human.md
graph = canonicalize_with_seed(..., central_graph)
...
central_graph = accumulate_central(central_graph, paper_graph)  # 约 396，篇末累积
```

`accumulate_central`（约 86 行）把 `reusable_subgraph(..., scope="global")` 并进 central。带 CCDC 的 `MetalOrganicPolyhedron` 算 global reusable（`is_cross_entity_reusable`，约 159 行）。

`1b9180ec/cs1/human.md` 里已经出现：

```text
id=ontomops:MetalOrganicPolyhedron-1
ontomops:hasCCDCNumber=1469174
rdfs:label=Zr-bpydc-CuCl2
```

那是 **50307a45** 的产物。convert 再沿 `isRepresentedBy` 把 1469174 写到 1b91 / 7ba 的几乎每个 Synthesis 上。503 自己的 `steps.json` 里还能看到 **1590348**（a014d993 的号）。

官方 Pipeline 的 reuse 是当前 DOI 作用域。v2 OX 的 central 是**整个 batch 全局库**。

**该怎么改：** 每篇论文开始时 `paper_graph = None` 已经有了；**`central_graph` 也按篇清空**，或至少不要把「带 CCDC 的 MOP」放进下一篇的 `CROSS_DOCUMENT_REUSABLE_ENTITIES`。溶剂 / species 跨篇是否保留，另说；这场 steps 对齐先切断 MOP+CCDC。

### C. publish 形状不对，官方 merge 吃错文件

**插这里：** `src/kg_building/ontologx/publish_runtime.py`

现在每个实体都写三份**同一张（或几乎同一张）图**：

```text
score_runtime/<hash>/ontosynthesis_output/<label>.ttl      ← main
score_runtime/<hash>/ontospecies_output/<label>.ttl        ← spliced（这场没跑 ontospecies）
score_runtime/<hash>/ontomops_output/ontomops_extension_<label>.ttl  ← spliced（这场没跑 ontomops）
score_runtime/<hash>/<hash>.ttl                            ← 论文级，往往更干净
```

**官方 convert 在 repo A（只读）：**

`scripts/merge_and_conversion_main.py` → `ttl_merge.merge_for_hash`（约 365 行）

`_gather_files_for_hash`（约 42 行）只收：

```text
<hash>/ontosynthesis_output/*.ttl     # 主图
<hash>/ontospecies_output/*.ttl       # 当 extension
<hash>/ontomops_output/*.ttl          # 当 extension
<hash>/cbu_derivation/integrated/*.ttl
```

**不读** `<hash>/<hash>.ttl`。所以论文级那份正确边被扔掉了。rdflib 合并 UMC-1.ttl + UMC-2.ttl 时，同一 IRI 上的两个 `rdfs:label` 都会留下 → 第 1 节那颗双标签节点。

v2 怎么调用（不要改 repo A）：

```text
src/kg_building/ontologx/cli.py --score
    → score_four.convert_runtime(data_dir=out_dir/score_runtime, ...)
    → repo A scripts/merge_and_conversion_main.py --data-dir score_runtime --hash <hash>
```

Pipeline 的正确形状（对标）：`runtime/<hash>/ontosynthesis_output/` **每个文件只有一个 ChemicalSynthesis、一颗独立产物 IRI**。没有 extension 就不要有 `ontospecies_output/` / `ontomops_output/`。

**该怎么改：**

1. 没跑 `--extension` 时，不要写 ontospecies / ontomops。
2. `ontosynthesis_output/<label>.ttl` 只含**当前实体**的主图，不要把已经 attach 过全篇的累积图写进去。
3. 论文级 `<hash>.ttl` 可以留着给人看，但 **convert 不会用它**，除非改 v2 的 `convert_runtime` 在无 extension 时直接 convert 这一份（仍调用 repo A 的 `--conversion`，不要改 A 的 merge 逻辑）。

---

## 3. 原本代码对照

| 东西 | 在哪 | 角色 |
|---|---|---|
| OX 并图 / reuse key | v2 `src/kg_building/ontologx/graph_merge.py` | **修这里** |
| 跨篇 central | v2 `src/kg_building/ontologx/run_loop.py` `accumulate_central` | **修这里** |
| 发布成 Pipeline 目录 | v2 `src/kg_building/ontologx/publish_runtime.py` | **修这里** |
| IRI 怎么拼 | v2 `src/kg_building/ontologx/ttl_export.py` `instance_iri` | 随 node.id；id 不撞 IRI 就不撞 |
| 官方 merge | repo A `scripts/output_conversion_ttl_to_json/ttl_merge.py` | **只读**。按 Pipeline 目录并 TTL |
| 官方 convert 入口 | repo A `scripts/merge_and_conversion_main.py` | **只读** |
| 产物名怎么抽 | repo A `scripts/output_conversion_ttl_to_json/ontosynthesis_step_conversion.py` `query_outputs`（约 90 行）+ `build_json_structure`（约 1042 行：`[synthesis label] + output labels`） | **只读** |
| steps 配对 | repo A `evaluation/scoring_steps.py` `_find_best_synthesis_match` / `_unique_label_anchor` | **只读**。空 CCDC + 重名 = 不配 |

不要在 repo A 的 merge 里加“OX 特例”。官方 30 篇 Pipeline 依赖现在这套目录约定。

---

## 4. 怎么用 / 怎么验

### 4.1 修完代码后：先不要重跑 LLM

身份和 publish 是图后处理。用现有 `ox_gs5_generic_strict_reuse/<hash>/cs*/` 的实体图，重新 publish → convert → score 就能验证配对是否恢复。

若现有 `cs*_main.ttl` 里第二实体已经指向 `ChemicalOutput-1`，只重 publish 不够，要按修过的 `attach_subgraph` **从各实体 main 再拼一遍 paper 图**，或重跑 OX（更贵）。先看 `0c57bac8/0c57bac8.ttl`：那份论文级已经是 1:1。最快探针：

```powershell
# 不要改 A。临时把论文级 TTL 当成唯一输入试 convert
# （只作诊断：证明 scorer 能配上，不是生产路径）
```

生产路径仍是：publish 成「一实体一文件、产物 IRI 不撞」的 `ontosynthesis_output/`，再走现在的 `--score`。

### 4.2 验收（0c57bac8）

1. `score_runtime/0c57bac8/ontosynthesis_output/UMC-2.ttl` 的 `hasChemicalOutput` **不是** `ChemicalOutput-1`。
2. `merged/0c57bac8/0c57bac8.ttl` 里 `ChemicalOutput` 节点只有一个 `rdfs:label`。
3. `merged/0c57bac8/steps.json`：一个 Synthesis 只有 `UMC-1`，另一个只有 `UMC-2`。
4. `scores/scoring_steps/0c57bac8.md` 不再是 `umc-1 (GT only)` / `(Pred only)`。F1 不必等于 Pipeline 0.723（UMC-1 论文级图只有 1 个 Add，那是真瘦），但 **不能再是 0.000**。
5. `1b9180ec` 的 `human.md` / TTL 里不能再出现 `Zr-bpydc-CuCl2` / `1469174`。
6. 没跑 extension 时，`score_runtime/<hash>/` 下没有 `ontospecies_output/`、`ontomops_output/`。

### 4.3 日常命令（不变）

```powershell
& "C:\Program Files\Python313\python.exe" -m src.kg_building.ontologx --protocol generic-strict `
  --hint-runs <PIPE_RUN> --hash <hash> `
  --out-dir scenarios/mops/runs/ox_<tag>_generic_strict_reuse --score
```

不要 `--from-main-run`，这场不要 `--extension`。换合同只改 `--protocol generic-noprompt`。

**一篇一篇的 `out-dir`，或修完 B 再共用 out-dir。** 未修 central 之前，5 篇写进同一个目录就会继续串 MOP。

---

## 5. 和分数怎么读

| 观察 | 含义 |
|---|---|
| OX steps 0.000 且 Pred 步骤数很多 | 配对事故（本文 A+C），不是空图 |
| Characterisation 两边 0.570、CBU 0 | 没跑 extension / ontomops，这场不要比 |
| a014d993 化学品 0.235、6–7 node 就 SHACL conforms | 图真瘦，修 merge 救不了 |
| Pipeline 0.769 vs official5 同篇更高 | 抽取账本 + 空防火墙，见 `docs/react_firewall_and_history_projection.md` |

修完 A+B+C 再看 Pipeline vs OX 的真差距。现在的 0.307 是低估。
