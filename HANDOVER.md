# OntoSynthesis LLM Generation Pipeline Handover

更新日期：2026-07-30

## 1. 当前目标

按固定依赖顺序逐层生成、验证并 repair：

`creation_base.py → creation_entities.py → creation_relationships.py → creation_checks.py → main.py → extraction prompts → KG prompts`

语义决策、诊断、代码生成和 repair 均由 LLM 完成。Orchestrator 只负责机械任务，例如结构化 edit 应用、import、运行时 probe、回滚和报告保存。不要增加 script-based semantic fallback，也不要在 T-Box 及其自动派生 contract 之外写入 domain-specific knowledge。

当前阶段聚焦 `main.py`：该暴露的 MCP tools 必须全部暴露，不该暴露的能力必须完全不可见。

## 2. 已完成且通过的 OntoSynthesis artifacts

本轮直接测试目录：

`runs/ontosynthesis_direct_entities_relationships_gpt5_20260730`

已经生成并通过 hard validation、行为 probe 和 LLM semantic review：

- `scripts/ontosynthesis/ontosynthesis_creation_base.py`
- `scripts/ontosynthesis/ontosynthesis_creation_entities.py`
- `scripts/ontosynthesis/ontosynthesis_creation_relationships.py`
- `scripts/ontosynthesis/ontosynthesis_creation_checks.py`

最终 checks 验收结果：

`runs/ontosynthesis_direct_entities_relationships_gpt5_20260730/direct_checks_final.json`

该报告为：

- `stage_ok: true`
- `semantic_decision: pass`
- `critical_errors: []`

本轮已处理的主要问题：

- fixed entity creator 会显式写入 ancestor `rdf:type`。
- parent creator 不会因为 ancestor typing 而错误复用 subclass individual。
- OM-2 creator 由 fixed runtime 提供，并按 T-Box relationship ranges 限定允许创建的 quantity classes。
- `creation_relationships.py` 中 `retrievedFrom` 的 malformed capability IRI 已修正。
- `creation_checks.py` 已限定 `__all__ = ["check_ordered_members"]`，并通过 ordered-member 行为验证。

## 3. 最后一次未完成的修改

已修改：

`src/agents/scripts_and_prompts_generation/pure_llm_generation.py`

新增了 `main.py` 的 closed-world MCP surface meta-contract：

- `_artifact_role_contract(main.py)` 现在要求 registry 与 allowlist 严格相等。
- `_artifact_generation_contract(main.py)` 会自动构造：
  - `lifecycle_tools`
  - `entity_tools`
  - `relationship_tools`
  - `check_tools`
  - `expected_mcp_tools`
- `_artifact_generation_guidance(main.py)` 明确禁止把 closed-world allowlist 当作“最小必需集合”。

重要：这项修改尚未完成测试，也尚未补齐 validator 的 exact-surface equality gate。`main.py` 尚未在本轮生成，不能声称已经通过验收。

## 4. main.py 的目标暴露面

允许暴露：

- lifecycle：
  - `init_memory`
  - `export_memory`
  - `materialize_hints`
- validated entities module 的公开 creator。
- validated relationships module 的 property-specific writer。
- validated checks module 的只读 checker。
- 当 T-Box relationship range 需要 OM-2 时，允许 bounded `create_om2_quantity`。

禁止暴露：

- fixed `rdf_runtime` module 或其任意直接 callable。
- retained graph、`rdflib.Graph` 对象或任意通用 graph 操作。
- unrestricted Turtle/file loader。
- capability factory、capability map 或 caller-selected dispatcher。
- caller-selected RDF class、predicate 或 triple writer。
- imported module、class、debug/reset/convenience tool。
- 任何不在自动派生 `expected_mcp_tools` 中的额外 tool。

不要通过函数名黑名单判断 generic capability。Hard gate 应验证实际 registry 与 T-Box/上游 manifest 自动派生 allowlist 的严格集合相等；LLM semantic reviewer 再根据 callable provenance、schema 和行为证据判断是否存在语义绕过。

## 5. 下一步必须完成

1. 检查 `pure_llm_generation.py` 最后新增的 main contract：
   - 确认 `relationship_tool_contracts` 的 `public_tool` 派生正确。
   - 确认 `create_om2_quantity` 只在 T-Box range 实际需要时加入。
   - 确认 checker allowlist 来自实际上游公开 manifest，而非不可靠假设。

2. 补强 `agentic_generation_validation.py`：
   - 三次独立 import/startup。
   - 获取真实 FastMCP registry inventory。
   - 每次都要求 `actual_tools == expected_mcp_tools`。
   - 报告 missing、extra 和三次 surface instability。
   - 检查注册 callable provenance；只允许 main lifecycle wrapper 或指定 validated sibling module 的公开 callable。
   - 检查 schema 不包含 caller-selected class/predicate/triple、Graph 或任意文件 loader 参数。
   - 保留 import、explicit signature、错误调用拒绝且不修改 graph 等 hard checks。

3. 为上述 contract 和 validator 增加 targeted tests：
   - 精确 allowlist 通过。
   - 缺少 approved tool 失败。
   - 多出 generic/runtime/convenience tool 失败。
   - tool 名称正常但 handler provenance 指向 runtime generic callable 时失败。
   - 三次启动 inventory 不一致时失败。

4. 基于本轮已验收的四个上游 artifacts，只生成 `main.py`：
   - 不重新生成 dependency。
   - 使用 `_generation_task(...)` 提供正式 meta-prompt 和自动派生 contract。
   - LLM 输出 exact edits 或 unified diff；orchestrator 只机械应用。
   - generation 后先 hard validation，再运行独立 LLM semantic review。
   - 失败时做 focused LLM diagnosis/repair，只修改 `main.py`。

5. 首个 `main.py` 通过后，再做三次独立生成稳定性测试；当前用户此前允许首轮只要求一次成功，但 main surface 的三次独立 startup probe 仍应在单次验收内执行。

## 6. 验收标准

`main.py` 只有同时满足以下条件才能冻结：

- 可 import，可启动 MCP registry。
- 三次独立 startup 暴露面一致。
- `actual_tools == expected_mcp_tools`，无 missing、无 extra。
- 每个 tool 的 callable provenance 和参数 schema 合法。
- 错误调用返回 structured rejection，且 graph 不发生 mutation。
- lifecycle 正确区分初始化新 graph 与恢复已有 graph。
- `materialize_hints` 不直接绕过 property/class-specific capabilities。
- LLM semantic reviewer 返回 `pass`，且无 critical errors。
- 生成代码不包含 T-Box 之外的 domain-specific assumptions。

## 7. 已知风险

- 当前 `_expected_tool_surface_report` 主要检查 required tools 是否存在，尚未执行完整 closed-world equality，因此 extra tools 仍可能漏过。
- 旧生成结果中存在自定义轻量 `MCPRegistry`，不能代表真实 FastMCP server 正确启动；本轮应验证真实 registry/API。
- `materialize_hints` 是最大绕过风险：即使 registry 表面正确，其内部仍可能直接调用 Graph mutation 或 unrestricted loader。
- 单纯检查 tool 名称不足以发现“安全名称绑定 generic handler”的情况，必须检查 provenance 和行为。
- 仓库有大量既有未提交及未跟踪文件。不要清理、覆盖或回滚与本任务无关的用户工作。

## 8. 当前进程状态

交接时没有正在运行的 LLM generation、repair、validator 或 evaluation 任务。最后完成的进程是 `creation_checks.py` 的复验，已正常退出。

## 9. 尚未执行

- 未生成本轮 `main.py`。
- 未运行 main hard/soft validation。
- 未运行 main focused repair。
- 未执行 neutral T-Box fresh generation。
- 未执行完整 package integration。
- 未执行 mock extraction、A-Box、HermiT 和 LLM semantic scoring 闭环。
- 未创建 git commit。
