# Scorer 位置和用法

对照仓库只读，不改源码、不改官方 pack、不写它的 `evaluation/data/`。

| 项 | 值 |
|---|---|
| Scorer 仓库 | `C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction` |
| 本仓库封装 | `src/kg_building/ontologx/score_four.py` |
| 默认路径 | `src/kg_building/experiment_protocol.py` 的 `DEFAULT_SCORER_REPO` |
| Python | Pipeline 打分跟当时的解释器；OX 用 `C:\Program Files\Python313\python.exe`。`cwd` 必须是对照仓库根 |
| GT | 对照仓库 `full_ground_truth/`（`--full`） |

转换脚本也在对照仓库：`scripts/merge_and_conversion_main.py`。`--data-dir` 指向本仓库 runtime，`--output-dir` 必须写在**本仓库该 run** 下。

## 一条命令（推荐）

```powershell
# Pipeline：协议跑完后打进该 run 的 merged/ scores/
python -m src.extraction_runtime ontosynthesis --protocol generic-strict `
  --config scenarios/mops/runs/<PIPE_RUN>/pipeline.resolved.json `
  --generation-run e2e1 --hash <hash> --resume --score

# OX：--score 先 convert 全部 hash，再一次 score_four
& "C:\Program Files\Python313\python.exe" -m src.kg_building.ontologx `
  --protocol generic-strict --hint-runs scenarios/mops/runs/<PIPE_RUN> `
  --hash <hash> --out-dir scenarios/mops/runs/ox_<tag>_generic_strict_reuse --score
```

写出：

- Pipeline：`scenarios/mops/runs/<PIPE_RUN>/merged/`、`.../scores/`
- OX：`scenarios/mops/runs/<OX_OUT>/merged/`、`.../scores/`

## 四个模块（与官方 30 篇 steps 对齐）

| 模块 | 旗标 |
|---|---|
| `evaluation.scoring_cbu` | `--full` |
| `evaluation.scoring_characterisation` | `--full` |
| `evaluation.scoring_steps` | `--full --skip-order --ignore --no-vessel --llm-synonyms --llm-synonym-model openai/gpt-5.6-sol` |
| `evaluation.scoring_chemicals` | `--full` |

共同：`--pred-root <本仓库 merged>` `--out-root <本仓库 scores/<module>>`。steps 再加 `--hash`（可重复）。

手跑 steps 示例（`cwd` = 对照仓库）：

```powershell
python -u -m evaluation.scoring_steps --full --skip-order --ignore --no-vessel `
  --llm-synonyms --llm-synonym-model openai/gpt-5.6-sol `
  --pred-root D:\MCP-enhanced-MOPs-Extraction_clean-v2\scenarios\mops\runs\<RUN>\merged `
  --out-root  D:\MCP-enhanced-MOPs-Extraction_clean-v2\scenarios\mops\runs\<RUN>\scores\scoring_steps `
  --hash a014d993 --hash 50307a45
```

读 `<out-root>/_overall.md` 的 Fine-grained 行。多 hash 必须一次传入，不要每篇打一次覆盖 `_overall.md`。

## LLM（steps）

| 层 | 默认 | 我们 |
|---|---|---|
| `--llm-synonyms` + `openai/gpt-5.6-sol` | 关；官方 30 篇开 | 开 |
| 产品身份 judge（`gpt-4o`） | 开；`--no-llm-product-match` 才关 | 保持默认开 |
| `--llm-fast-match` | 关 | 关 |
| `--correct-ccdc-by-name` | 关 | 关 |

产品 judge 不能把 `UMC-1` 配到 Pred 里同时写了 `UMC-1`+`UMC-2` 的那一行；那是硬冲突，不是没开 LLM。

同义词要对照仓库 `.env` 的 `REMOTE_BASE_URL` / `REMOTE_API_KEY`。不要用 `scoring_all`。这场 steps 对齐不要加 `--extension`（CBU 会是 0）。
