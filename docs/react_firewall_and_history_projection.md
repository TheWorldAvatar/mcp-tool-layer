# Pipeline ReAct：防火墙 + history projection

给本仓库（repo B / v2）用。对照仓库（repo A）不要改：

`C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction`

这两套钩子**只服务 Pipeline 的 MCP ReAct**。OX 不走 `create_react_agent`，没有这两件事。

官方 30 篇 / official5 开的是 repo A 里的真实现。v2 的 `--protocol generic-strict` / `generic-noprompt` 已经把同名旗标打成 True，但钩子是空的。旗标亮着 ≠ 行为对齐。

---

## 1. 各干什么

| 名字 | 何时跑 | 作用 |
|---|---|---|
| **History projection** | 每轮模型调用前（`pre_model_hook`） | 已完成的 tool 轮压成短 receipt（status / iri / already_committed / fingerprint）。完整 AI/Tool 消息留在 LangGraph state / 审计 trace，**下一轮模型只看见压缩后的 `llm_input_messages`**。不改图，不停循环。 |
| **Argument firewall** | 模型刚写出 tool_calls 之后、工具执行之前（`post_model_hook`） | 对照生成包里的 ownership 合同，删掉当前 `create_*` **不允许**的 kwargs，留下合法子集再执行，并挂 `ARGUMENT_FIREWALL_SANITIZED`。**不**把删掉的参数转发到别的工具。 |
| **Loop guard**（和防火墙挂在同一个 post-hook） | 同上 | no-progress / graph-cycle 停环。重复 committed 在新合同下一般放行，让 MCP 回 `already_committed`；只有旧 v4 fallback 才会立刻停。 |

防火墙不是“把错调用整段丢掉”，而是 **preserve legal subset**。History projection 不是“强制停”，停环靠 no-progress / cycle / MCP 的 `already_committed`。

---

## 2. 原本代码在 repo A 哪里

全部在一个文件里：

`C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction\models\BaseAgent.py`

不要把整个 `BaseAgent` 抄进 v2。v2 已有自己的 `src/extraction_runtime/agent/client.py`。要搬的是**钩子函数和它们的依赖**，接到现有 hook 槽上。

### 2.1 必须搬的函数（按依赖）

行号是 2026-09-07 对照时的位置，搬之前以函数名为准再对一次。

**合同加载**

| 函数 | 大约行 | 读什么 |
|---|---|---|
| `_loop_guard_contract_path` | 512 | `$TWA_GENERATED_ARTIFACT_ROOT/scripts/$TWA_MAIN_ONTOLOGY_NAME/_occurrence_loop_guard.json` |
| `load_occurrence_loop_guard_contract` | 523 | 上面的 JSON；没有则用 `_V4_FALLBACK_LOOP_GUARD`（约 498 行） |
| `load_occurrence_argument_ownership_contract` | 540 | 同目录 `_occurrence_argument_ownership.json`；没有则 `{}`（防火墙变成空操作） |
| `_argument_contract_indexes` | 568 | 建成 `tool → allowed kwargs` 和 `argument → owner tools` |
| `_loop_guard_identity_index` | 595 | 建成 `tool → identity_args` |

**History projection**

| 函数 | 大约行 |
|---|---|
| `_compact_receipt_value` | 93 |
| `_canonical_args_sha256` | 111 |
| `_canonical_semantic_identity` | 122 |
| `_semantic_call_fingerprint` | 135 |
| `_looks_like_argument_validation_error` | 162 |
| `_normalized_tool_error_payload` | 186 |
| `project_react_history_to_receipts` | 293 |

`project_react_history_to_receipts` 的返回值必须是：

```python
{"llm_input_messages": projected}   # 或未压缩时的 messages
```

不要 `return state`。LangGraph 靠 `llm_input_messages` 只改模型输入，不改审计 messages。

**防火墙 + 停环（同一个 post-hook）**

| 函数 | 大约行 |
|---|---|
| `_occurrence_identity` / `_fallback_call_identity` / `_call_guard_identity` | 617–652 |
| `_parsed_tool_receipt` | 655 |
| `_no_progress_threshold` / `_completed_react_turns` / `_explicit_no_progress_turn` | 665–732 |
| `_forced_extra_argument_repair` | 734 | 防火墙**关**时的旧修理路径；官方 protocol 开防火墙时不会走到这里 |
| `_rewrite_latest_call_through_argument_firewall` | 845 | **防火墙本体** |
| `_no_progress_stop` | 949 |
| `_graph_cycle_stop` | 996 |
| `stop_repeated_committed_output_calls` | 1059 | post-hook 入口 |

`stop_repeated_committed_output_calls` 无事返回 `{}`；改写或停环返回 `{"messages": [...]}`。不要 `return state`。

### 2.2 repo A 怎么接到 ReAct 上

同一文件 `BaseAgent.run`，大约 1720–1770 行：

```python
def occurrence_pre_model_hook(state):
    return project_react_history_to_receipts(
        state,
        classify_argument_owner_mismatch=react_argument_firewall,
    )

def occurrence_post_model_hook(state):
    return stop_repeated_committed_output_calls(
        state,
        argument_firewall=react_argument_firewall,
    )

create_react_agent(
    ...,
    pre_model_hook=occurrence_pre_model_hook if react_history_projection else None,
    post_model_hook=(
        occurrence_post_model_hook
        if react_history_projection or react_argument_firewall
        else None
    ),
)
```

官方 Pipeline 从 `src/pipelines/main_kg_building/build.py`（约 6591–6596 行）传入：

```text
react_history_projection = config["kg_react_history_projection"]
react_argument_firewall  = config["kg_argument_firewall_experiment"]
```

官方实验这两个 config 都是 `true`。

### 2.3 合同 JSON 从哪来（两边生成器都有）

生成 MCP 时写进 scripts，不是手写。

| 文件 | repo A 写出 | repo B 写出 |
|---|---|---|
| `_occurrence_argument_ownership.json` | `src/agents/scripts_and_prompts_generation/occurrence_surface_scripts.py` → `emit_occurrence_argument_ownership` | `src/kg_building_mcp_generation/emit/scripts.py` + `emit/sidecars.py` |
| `_occurrence_loop_guard.json` | 同上 `emit_occurrence_loop_guard` | 同上 |

运行时路径（两边约定一样）：

```text
$TWA_GENERATED_ARTIFACT_ROOT/scripts/$TWA_MAIN_ONTOLOGY_NAME/_occurrence_argument_ownership.json
$TWA_GENERATED_ARTIFACT_ROOT/scripts/$TWA_MAIN_ONTOLOGY_NAME/_occurrence_loop_guard.json
```

e2e1 生成包已经有这两份，例如：

```text
generated/runs/20260907_115911_e2e1/scripts/ontosynthesis/_occurrence_argument_ownership.json
generated/runs/20260907_115911_e2e1/scripts/ontosynthesis/_occurrence_loop_guard.json
```

v2 runner 已经会设环境变量（`src/extraction_runtime/runner.py`）：

```text
TWA_GENERATED_ARTIFACT_ROOT=<generated/runs/...>
TWA_MAIN_ONTOLOGY_NAME=ontosynthesis
```

合同在、钩子空，等于白生成。

---

## 3. 插到 repo B 哪里

### 3.1 换空实现（主插入点）

`src/extraction_runtime/agent/loop.py`

现在：

```python
def project_react_history_to_receipts(state, **_unused):
    return state   # 空

def stop_repeated_committed_output_calls(state, **_unused):
    return state   # 空
```

把 repo A 第 2.1 节那些函数搬到这个文件（或同目录新建 `react_hooks.py`，再从这里 re-export）。公开名字保持这两个，v2 其它地方不用改 import。

`loop.py` 里已有 `normalize_ai_message_content`。repo A 同名函数带下划线（`_normalize_ai_message_content`）。搬的时候二选一，不要两套。

### 3.2 把旗标传进钩子（现在没传）

`src/extraction_runtime/agent/client.py` 约 183–187 行，现在是：

```python
def occurrence_pre_model_hook(state):
    return project_react_history_to_receipts(state)

def occurrence_post_model_hook(state):
    return stop_repeated_committed_output_calls(state)
```

改成和 repo A 一样，把 `react_argument_firewall` 传进去（见 2.2）。`create_react_agent` 的 pre/post 开关逻辑已经对了，不用改。

### 3.3 调用链（已经接通，不要再手拼）

```text
--protocol generic-strict | generic-noprompt
    → src/kg_building/experiment_protocol.py
         react_history_projection = True
         react_argument_firewall  = True
    → src/kg_building/pipeline/main_kg/agent.py
         agent.run(..., react_history_projection=True, react_argument_firewall=True)
    → src/extraction_runtime/agent/client.py
         pre/post hook 挂上
    → src/extraction_runtime/agent/loop.py
         目前 identity；插真实现就在这里
```

`experiment_protocol.py` 和 `main_kg/agent.py` **不用为了开旗标再改**。缺的是 loop 里的函数体和 client 里的参数转发。

### 3.4 不要插的地方

| 位置 | 原因 |
|---|---|
| `src/kg_building/ontologx/*` | OX 不是 ReAct，没有 tool_calls 可过滤 |
| repo A `models/BaseAgent.py` | 对照仓库，只读 |
| 官方 score pack / 冻结抽取 | 无关 |
| Pipeline user 信封 | 防火墙不写进 prompt；合同在 JSON + hook |

---

## 4. 怎么用

### 4.1 日常实验（插好之后）

和现在一样，只传 `--protocol`。不要再手写 seed / 模型 / 信封 / 防火墙开关。

```powershell
python -m src.extraction_runtime ontosynthesis --protocol generic-strict --generation-run e2e1 --hash <hash> --tag gs
```

`--generation-run` 必须指向带那两份 JSON 的生成包。`runner.py` 会设 `TWA_GENERATED_ARTIFACT_ROOT`。没有这份 JSON，防火墙加载到 `{}`，所有 kwargs 都算“不在合同里的工具”，rewrite 直接跳过，等于没开。

### 4.2 单测 / 临时关

`BaseAgent.run` 两个参数默认仍是 `False`。只有 `main_kg/agent.py`（以及 `--protocol`）会打开。

关某一侧：改 `run_kg_agent` 里对应参数，或改 `ExperimentProtocol` 默认值。不要在 CLI 再加一套平行开关。

### 4.3 环境（进程和 MCP child 都要有）

`--protocol` 已经锁：

```text
TWA_LLM_SEED=42
TWA_MCP_TOOL_DESCRIPTIONS_ENABLED=0
TWA_SEMANTIC_OPERATION_SURFACE=1
```

钩子另外依赖（runner 已设）：

```text
TWA_GENERATED_ARTIFACT_ROOT=<本趟 generated/runs/<id>>
TWA_MAIN_ONTOLOGY_NAME=ontosynthesis
```

### 4.4 插好之后怎么验收

1. **合同能读到。** 构图进程里  
   `Path(os.environ["TWA_GENERATED_ARTIFACT_ROOT"]) / "scripts" / "ontosynthesis" / "_occurrence_argument_ownership.json"`  
   存在，且 `tools[].allowed_arguments` 非空。
2. **防火墙真的改过调用。** 找一篇会乱填 kwargs 的实体，trace / receipt 里出现 `argument_firewall_warning` 或 `ARGUMENT_FIREWALL_SANITIZED`；被删的参数**没有**出现在随后的 MCP 执行 args 里。
3. **History 真的压缩了。** 第二轮起，模型输入不再是完整 tool JSON，而是 `AUTHORITATIVE MCP STATE` + receipt。`prompts/kg_building/*.user.md` 仍是 DOI / 根 IRI / hints（user 信封不变；压缩的是 ReAct 中间轮）。
4. **OX 分数不应因这次插入而变。** 没接线。若变了，插错文件了。

---

## 5. 和当前 5 篇 generic-strict 的关系

这 5 篇 Pipeline 构图时旗标已是 True，钩子是空的。所以：

- 不能拿这 5 篇 Pipeline 分去对 official5 的绝对值（official5 是真防火墙）。
- 插进真钩子之后必须**重跑 Pipeline main KG**，不能只 rescore。防火墙改的是构图时的 tool 调用，TTL 已经写死了。
- OX 那侧的 0 分是产物 IRI / 跨篇 central reuse / publish 形状，和这两套钩子无关。见当时查分记录，不要在这篇里修 OX。

---

## 6. 推荐搬法（一次做完、不要改 A）

1. 把 2.1 的函数原样放进 `src/extraction_runtime/agent/loop.py`（或 `react_hooks.py`）。
2. `client.py` 按 3.2 把 `react_argument_firewall` 传进两个钩子。
3. 用 e2e1 生成包跑 **一篇** `0c57bac8` 的 `--protocol generic-strict` main KG，按 4.4 验收。
4. 再决定要不要重跑另外 4 篇。

不要从 v2 去改 repo A，也不要把 A 的整个 `BaseAgent` 换进来覆盖 v2 client（MCP session / `mcp_instruction_in_user` / required init-export 已经在 v2 接好了）。
