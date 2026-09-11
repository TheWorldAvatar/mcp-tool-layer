# Scorer 配对对 OX 不公平在哪里

只讲**问题**。不讲改法。对照数字来自锁定的 generic-noprompt v2（`s2nv230` / `s2nv2x30`，`s3nv230` / `s3nv2x30`）。打分旗标：`--full --skip-order --ignore --no-vessel --llm-synonyms`。

相关背景：[handover_kg_protocols_scorer_20260909.md](handover_kg_protocols_scorer_20260909.md)。四档协议不是本文范围。

---

## 1. 这篇在说什么

Steps 打分不是「拿整篇图跟 GT 比一下」。它先把 GT 的每个 `Synthesis` **一对一**配到预测里的一个 `Synthesis`，再比步和字段。

配不上的两边都会进 `_overall.md` 的实体表，标记 **GT only** 和 **Pred only**。那一行的步全部变成 FN / FP。一篇论文如果只有一个合成实体，整篇 Fine-grained F1 就是 **0.000**。type-only 也会是 0，因为根本没有配上的实体可数类型。

这和「OX 没建图」不是一回事。图可以有 Add、有 hop、有 11 个 step，照样因为**名字没配上**被记成零分。

本文说的不公平：同一套转换 + 同一套身份门槛，对 Pipeline 的导出文件名和 OX 的人话工序句**处理方式不对称**。OX 越是把路线写清楚，越容易把产品身份算脏。

---

## 2. 干净对照：`3f239659`（DOI `10.1039/C5RA26357C`）

这是目前最干净的一篇。s2 和 s3 各打一次，结果一样。

| | Pipeline | OX |
|---|---|---|
| 锁定分目录 | `20260909_160339_s2nv230` / `20260909_160900_s3nv230` | `20260909_160544_s2nv2x30` / `20260909_161102_s3nv2x30` |
| Fine-grained F1 | **0.954**（TP=62 FP=2 FN=4） | **0.000**（TP=0 FP=44 FN=53） |
| type-only（配上之后才会有） | 11/11 = 1.000 | 没有配上的实体 |
| `_overall.md` 实体行 | `[cu12(tmta)8(h2o)12] nanocage` 配上 11 步 | GT only `synthesis_of_[cu12…]_(1)` **和** Pred only 整句工序 |

OX 的图不是空的。s2 合并 TTL `ox_s2nv2_30/3f239659/3f239659.ttl`（以及打分用的 `merged/3f239659/3f239659.ttl`）里：

- ChemicalSynthesis 的 `rdfs:label`：`Synthesis of [Cu12(TMTA)8(H2O)12] nanocage (1) from TMTAH3 and CuCl2·2H2O in DMF/ethanol with KOH`
- 另有产物节点标签 `[Cu12(TMTA)8(H2O)12]`
- 多个 Add（TMTAH3 / CuCl2 / DMF / ethanol / KOH）以及 Stir / Evaporate / Filter / Dry

转换后的 `merged/3f239659/steps.json` 两边都有 **11 个 step**。OX 的 `addedChemical` 不是空的。Scorer 仍然把它们当成两个无关实体。

Pipeline 同一篇的 CS 标签不是人话，是导出文件名：

```text
Synthesis_of_Cu12_TMTA_8_H2O_12_nanocage_1_from--8c25330d4147
```

转换后 Pipeline 的 `productNames` **只剩** `[Cu12(TMTA)8(H2O)12] nanocage`。OX 的 `productNames` 是：

```text
[
  "Synthesis of [Cu12(TMTA)8(H2O)12] nanocage (1) from TMTAH3 and CuCl2·2H2O in DMF/ethanol with KOH",
  "[Cu12(TMTA)8(H2O)12] nanocage"
]
```

GT 这一侧是：

```text
productNames:
  synthesis_of_[Cu12(TMTA)8(H2O)12]·8CH3CH2OH·40H2O_(1)
  [Cu12(TMTA)8(H2O)12]·8CH3CH2OH·40H2O
  MetalOrganicPolyhedron for [Cu12(TMTA)8(H2O)12]·8CH3CH2OH·40H2O
  Cage 1
productCCDCNumber: "1439771"
```

两边预测的 `productCCDCNumber` 都是空字符串。GT 的 CCDC **没有**把 OX 救回来，也没有把 Pipeline 救回来；Pipeline 不需要它，OX 需要它。

---

## 3. 不公平从哪一层开始：转换把两种身份塞进同一个名单

`ontosynthesis_step_conversion.py` 从一个 ChemicalSynthesis 收两类字符串：

1. **路线 / 工序身份**：CS 自己的 `rdfs:label`（OX 常写成 “Synthesis of X from A and B in solvent”；Pipeline 常写成 `Synthesis_of_X--<hex>`）
2. **产物身份**：`hasChemicalOutput` 上 ChemicalOutput / Species / MOP 的 `rdfs:label` 和 `hasAlternativeNames`

锁定这次打分用的 merged JSON 里，这两类都被写进 **`productNames`**。CS 标签排在前面。Scorer 再把 `productNames` 当成「这是什么产品」。

字段设计从一开始就把配对要的东西混在一起：

- 产品是谁（公式、cage 1、CCDC）
- 这条路线怎么做（from 哪些试剂、via mechanochemical、in DMF/ethanol）

OX 按 T-Box 习惯把路线写进人话 label，这是合法图。转换没有把这条 label 排除在产品名单之外。Pipeline 的同类字段恰好是哈希文件名，后面有一条只认得这种文件名的清洗。

---

## 4. 清洗规则是 Pipeline 形状的，不是构图器中立的

`name_utils.is_hashed_artifact_label` 只认这种模式：

```text
.+--[0-9a-f]{8,16}$
```

也就是 Windows 截断导出茎 `Name--8c25330d4147`。

`filter_product_names` / merge 时的 `_drop_hashed_synthesis_alias_labels` / `prefer_synthesis_label` 都围着这条规则转：

- 名单里如果同时有哈希茎和「人话」，丢掉哈希，留下人话
- 「人话」之间再挑 **最长** 的一条（`prefer_synthesis_label`）

单测写得很直白：`tests/test_ttl_merge_hashed_synthesis_labels.py`

- 哈希茎 `Synthesis_of_Co24_C-pentylpyroga--8816a66c81dd` → 丢掉
- 长工序句 `Synthesis of [Co24…] nanocapsule (1) by reacting … in DMF/methanol …` → **留下**

所以：

| 构图器实际写在 CS `rdfs:label` 上的东西 | 清洗之后进 `productNames` 的东西 |
|---|---|
| Pipeline：`…nanocage_1_from--8c25330d4147` | 整段丢掉。只剩 ChemicalOutput 短名 |
| OX：完整英语工序句 | 被当成优质人话标签，**整句留下**，而且还排在第一位 |

OX **不会**写出 `--<12-hex>` 这种导出茎，所以永远吃不到这条「把脏 CS 标签扔掉」的规则。Pipeline 几乎篇篇都吃得到。s2 nv2 的 merged 统计：Pipeline 87 个合成实体里 **0** 条哈希名残留（已经洗掉）；OX 87 个里 **0** 条哈希名（本来就没有），但有多条 `from` / `via` 工序句留在 `productNames[0]`。

`prefer_synthesis_label` 选最长人话，对 OX 是反向的：句子越长、试剂写得越全，越容易当「主标签」留下。

---

## 5. 身份门槛吃的是并袋 token，不是「有没有一条对得上的产品名」

`evaluation/scoring_steps.py` 的 `_synthesis_identity_score`：

1. 把预测（以及 GT）的名字全部归一化
2. 每条名字切成 `[a-z0-9]+` token
3. **所有名字的 token 并成一个集合**
4. 相似度 = `round(2000 * |A∩B| / (|A|+|B|))`（实现里叫 Jaccard，公式其实是 Dice 标到 2000）
5. 精确重叠、titled-alias、或这个分数 **≥ 450**，才算 `name_related`
6. 否则还要看唯一 CCDC、唯一短码（`vmot-2`、`cage 1` 这类）。都没有 → **这条边直接丢掉**，LLM 裁判如果也没判成同一产品，就配不上

停用词非常短：`a, an, the, of, for, by, via, synthesis, synthesized, synthesised, method, route`。

**没有**列入停用词的包括：`from`, `and`, `in`, `with`, `nanocage`，以及全部试剂 / 溶剂。

`_strip_synthesis_title_prefix`（去掉开头的 `synthesis of`）只用于短码和 titled-alias，**不用于**这袋 token。并袋里 `synthesis` / `of` 会被停用词丢掉，但 `from TMTAH3 and CuCl2 in DMF/ethanol with KOH` 全部留下。

用锁定 merged 里的真实 `productNames`、关闭工序切分、只做哈希过滤之后，对 `3f239659` 的硬门槛是：

| 预测名单 | 并袋 Dice×1000 | 相对门槛 450 | 预测侧多出来的 token |
|---|---:|---|---|
| Pipeline：只有 `[Cu12(TMTA)8(H2O)12] nanocage` | **625** | 过 | `nanocage` |
| OX：工序句 + 同一个 nanocage | **444** | **不过** | `from, tmtah3, and, cucl2, 2h2o, in, dmf, ethanol, with, koh, nanocage` |
| 交集（两边都有） | — | — | `cu12, tmta, 8, h2o, 12`；OX 另外还撞上 GT 的 `1` |

OX 并不是缺产物名。第二条 `productNames` 已经是 `[Cu12(TMTA)8(H2O)12] nanocage`，和 Pipeline 配上的那条一样。并袋把**第一条工序句里的试剂溶剂**加进分母，444 比 450 低 6 个内部单位。整篇 11 步从「可以按字段打分」变成 Pred-only。

Pipeline 如果**不**丢哈希茎、同时又留着 ChemicalOutput，哈希规则仍会把茎滤掉，结果还是 625。不公平不在「Pipeline 名字更像 GT 公式」，而在 **OX 多写的那句合法路线信息被当成产品噪声**。

---

## 6. 同一套规则是抽签，不是稳定的身份比较

s2 OX merged 里，`productNames[0]` 带 `from` / `via` 的合成实体不止 `3f239659`。例如：

| hash | `productNames[0]`（缩短） | 锁定 steps 结果 |
|---|---|---|
| `3f239659` | `Synthesis of [Cu12(TMTA)8…] nanocage (1) from TMTAH3 and CuCl2…` | **整篇 F1 0.000**，GT only + Pred only |
| `b0046ae2` | `[{Cu12(TMBTA)8…}] MOP-TO synthesis under solvothermal conditions from TMBTA, Cu(NO3)2…` | **配上了**（type-only 8/8，F1 0.872；Pipeline 0.923） |
| `736dc58b` | `… via mechanochemical / solvothermal method` | 路线句较短，产物短名仍在名单里 |
| `a014d993` | `Structural transformation from VMOP-α to VMOP-β`、`Synthesis of VMOP-alpha from …` | 另有多实体 / 多路线问题，不是这篇的干净对照 |

`b0046ae2` 能配上，是因为 GT 产物名本身就是那条很长的公式，`TMBTA` / `DMA` / `H2O` 等 token 在 GT 袋里已经有了，试剂稀释不够把分数打到 450 以下。`3f239659` 的 GT 公式带的是结晶溶剂 `8CH3CH2OH·40H2O`，和 OX 工序句里的 `TMTAH3` / `DMF` / `KOH` 对不上，稀释就过了门槛。

所以：OX 写不写工序句、GT 公式里碰巧有没有同一批试剂词，决定这篇是 0.95 还是 0.00。这不是「产品是不是同一个」的稳定判定。

---

## 7. CCDC 救不了 OX，也救不了这篇里的不对称

配对顺序里，**两边都恰好出现一次的 CCDC** 是硬锚。GT `3f239659` 写了 `1439771`。

转换只从 `hasChemicalOutput` 下游的 OntoSpecies / OntoMOPs CCDC 属性取值。这篇两边转换后 `productCCDCNumber` 都是 `""`。s2 nv2：OX 87 个合成里 82 个空 CCDC，Pipeline 83 个空。CCDC 在 steps 上基本不是主锚。

名字里要出现字面 `CCDC-123` 才会走 `_synthesis_ccdc` 的名字回退。OX 工序句和 Pipeline 哈希名都没有这段。

结果：Pipeline 靠「洗掉脏 CS 标签 → 短产物名 → 625 ≥ 450」配上；OX 短产物名其实也在名单里，但被工序句稀释到 444，又没有 CCDC 锚，整篇零分。**缺 CCDC 是两边共同的**；**缺了之后谁还能靠名字活下来，是不对称的**。

---

## 8. LLM 产品裁判不是对称安全网

`score_four.py` **没有**传 `--no-llm-product-match`。默认会开 gpt-4o `same_product`。`_synthesis_identity_score` 只在精确名和 titled-alias 都为 0 时才问 LLM；问了且判同一产品，`identity_score[0] ≥ 1`，可以绕过 450。

锁定结果里 `3f239659` 仍然是 GT only / Pred only。说明这条边上 LLM **没有**把它们判成同一产品（判否，或调用失败 fail-close：`equivalent=false`）。Repo A 的 `evaluation/cache/product_identity_judge/` 当时是空的，无法从缓存读出判词。

政策原文只要求忽略 *“synthesis of”* 和标题里的温度。它**没有**要求忽略 `from TMTAH3 and CuCl2 in DMF/ethanol with KOH` 这种试剂名单。OX 交给裁判的是「产品名列表」，其中第一条就是整句配方。Pipeline 交给裁判的是一条短 nanocage。两边输入形状不同。

短码锚（`vmot-2`、括号里的 `1`）也救不了这篇：GT 有 `Cage 1`，OX 有 `nanocage (1)`，但并袋门槛已经先把边丢掉的路径上，短码必须在**切出来的产品身份名**上唯一命中。工序句稀释发生在同一套名字列表里。

---

## 9. 锁定分数之后，代码里还留下什么不对称

锁定 30 篇用的 merged JSON **没有** `synthesisLabel` 字段。工序句就在 `productNames[0]`。上面的 444 / 625 就是那场分。

之后转换开始把 ChemicalOutput 和 CS 标签拆开（`synthesis_product_fields`）。打分却又用 `_declared_synthesis_names` 把 `productNames` **和** `synthesisLabel` 拼回去，再送进同一套切分 / 并袋。拆字段并没有让配对视野变干净。

后来加的 `from` / `via` / `by reacting` / `in DMF/ethanol` 正则（`name_utils._ROUTE_TAIL`）是按**英语句式**猜哪一段是试剂尾巴。它不是构图器中立的身份定义，问题包括：

- OX 下一篇完全可以写成没有 `from` 的句子、把试剂放在前面、用别的介词、或把产物公式写在句尾；正则就不切或切错
- `transformation from A to B` 要靠另一条「不要切」的特例；和「要切 from 试剂」叠在同一套猜句子的逻辑上
- Pipeline 脏标签靠的是稳定的 `--<hex>` 结构；OX 脏标签靠的是不稳定的英语

即便按当前正则把 `3f239659` 的试剂尾巴切掉，身份分会变成 706，这篇能配上。这只说明 **444 失败来自那袋试剂 token**，不说明「按英语切工序」已经是公平规则。其它写法、其它语言、没有 ChemicalOutput 只剩一句 CS 标签的图，仍然压在同一套不对称假设上。

并袋本身还在：多条产品名的 token 仍是并成一大袋再比。OX 只要有任何一条长名字进身份列表，就会稀释其它短名。

---

## 10. 不要和这些问题混在一起

下面这些也会让 OX F1 变低，但**不是**这篇说的配对不公平：

- Add 上没有 `hasAddedChemicalInput`，药挂在合成层 `hasChemicalInput` → 转换读 hop → `addedChemical: []`（真图 / 真转换路径）
- SHACL 失败后 `budget_exhausted` 仍把非法图送去打分
- 配方截断、少建了一条合成实体
- 产品代码写错（例如 GT `tma-vmot-3`、预测却带 `TMA-VMOC-P-4`）触发 `_has_conflicting_product_index`，两边都可能配不上
- 多晶型 / 第二条 IRMOP-51 没建出来

`1b9180ec`、`d5ff239e` 在 s2 OX 的 missing-GT 表里，Pipeline 对应实体也有 GT only。那些不是「OX 工序句被并袋打死」的对照。

配对不公平的干净对照就是 **`3f239659` 在 s2 和 s3 上各一次 0.954 vs 0.000**。图在，步在，产物短名也在；差的是 CS 标签被当成产品名之后，OX 的合法工序句越过了 450，Pipeline 的哈希文件名被丢掉了。

---

## 11. 代码和结果锚点

| 东西 | 位置 |
|---|---|
| 身份并袋 / 450 门槛 | Repo A `evaluation/scoring_steps.py`：`_synthesis_identity_score`、`_synthesis_match_candidates` |
| 哈希茎 vs 人话 | `scripts/output_conversion_ttl_to_json/name_utils.py`：`is_hashed_artifact_label`、`filter_product_names`、`prefer_synthesis_label` |
| 锁定 JSON 仍把 CS 标签放进 `productNames` | 工作仓 `…/20260909_160544_s2nv2x30/merged/3f239659/steps.json` vs `…/20260909_160339_s2nv230/merged/3f239659/steps.json` |
| OX / Pipeline 原始 CS 标签 | `ox_s2nv2_30/3f239659/3f239659.ttl`；Pipeline merged TTL 里的 `Synthesis_of_Cu12_…--8c25330d4147` |
| 实体没配上的报告 | `…/s2nv2x30/scores/scoring_steps/_missing_gt_entities.md`、`_overall.md` |
| 单测写明「长工序句留下、哈希丢掉」 | `tests/test_ttl_merge_hashed_synthesis_labels.py` |
| LLM 政策 | `evaluation/utils/product_identity_judge.py` 的 `_POLICY`；失败 fail-close |

Headline 影响：`3f239659` 一篇从 0 变成接近 Pipeline 的 0.954，会抬 s2/s3 OX 的 micro-F1，但**解释不了**整包 0.10+ 的缺口。公平问题和建图缺口是两件事；前者会让「OX 更差」的表里掺进假零分。
