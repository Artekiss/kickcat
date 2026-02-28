# KickCat v1.3 TODOs

> 注：本文档包含 v1 到 v1.3+ 的历史里程碑；代码中的 `version` 字段指状态 schema 版本。

> 目标：先打通最小可运行闭环，再补齐测试与交付说明。

## 开发原则（新增，强制执行）

- 实现任意函数时，同步编写该函数的单元测试用例。
- 测试用例一旦通过并入主线，后续默认不修改。
- 仅当测试用例存在严重错误（与需求冲突、断言明显错误、无法稳定复现）时，才允许修订测试。
- 项目推进严格遵循本文件顺序执行，直至开发完成且测试全绿。

## P0 - 核心实现（先做，阻塞后续）

1. [x] 创建最小目录与四个文件骨架（`skills/kickcat/SKILL.md`、`skills/kickcat/kickcat.py`、`data/kickcat.json`、`HEARTBEAT.md`）
2. [x] 实现 `kickcat.py init`（幂等初始化，不覆盖已有状态）
3. [x] 实现 `kickcat.py tick`（10 分钟推进、clamp、候选动作计算）
4. [x] 实现 `kickcat.py apply` 统一 reducer（`pet_delta` / `task_add` / `mood_add` / `touch_interaction`）
5. [x] 在 `apply` 补齐规则与保护（字段/范围校验、去重、投喂冷却、同消息防重、clamp）
6. [x] 实现 `kickcat.py summary`（heartbeat/skill 使用的短 JSON）

## P1 - 文档契约（实现后立即补齐）

7. [x] 编写精简 `skills/kickcat/SKILL.md`（结构化 JSON 优先、reducer 映射、apply 调用顺序、回复风格限制）
8. [x] 编写极短 `HEARTBEAT.md`（`tick -> summary -> HEARTBEAT_OK` 或单条短消息）

## P2 - 测试设计与用例（与实现并行补齐）

9. [x] 设计 v1 测试策略与覆盖矩阵（命令层 + reducer 规则）
10. [x] `init` 测试：首次创建与幂等性
11. [x] `tick` 测试：10 分钟步进、互动减无聊、高饥饿/高无聊扣幸福、数值 clamp
12. [x] 候选动作测试：`task_reminder` 优先级、随机互动门槛、`none` 分支
13. [x] `apply pet_delta` 测试：字段合法性、单次限幅、clamp
14. [x] 投喂测试：`feed_strength` 映射、5 分钟冷却、同消息 hash 防重复
15. [x] `task_add` 测试：必填补齐、可选时间校验、30 分钟提醒去重
16. [x] `mood_add` 测试：范围校验、`id/created_at` 自动补齐
17. [x] `touch_interaction` 测试：UTC ISO 8601 时间戳更新
18. [x] `summary` 测试：输出结构与关键提示字段
19. [x] CLI 集成测试：`init -> apply -> tick -> summary` 最小 happy path

## P3 - 验证与交付（收尾）

20. [x] 完成手动闭环验证（闲聊、投喂与冷却、任务添加、时间提醒、空闲心跳）
21. [x] 先跑最小相关测试，再跑全量测试并记录缺口
22. [x] 整理交付说明（文件列表、作用、初始化、手测命令、最小示例、v1 简化点）

## 执行顺序说明

- 主线顺序：`P0 -> P1 -> P2 -> P3`
- 并行建议：`P2` 可从 `P0` 第 2 步开始同步编写，不阻塞核心实现
- 完成标准：以 `P3` 全部完成且闭环验证通过为准

## 迭代二：OpenClaw 部署与调试增强（社区实践）

1. [x] 增加 `deploy/debug` 模式：默认 `deploy`，支持 `--mode` 与环境变量覆盖
2. [x] 增加 `--debug-log-file` 与环境变量覆盖，异常时写入 jsonl 日志
3. [x] 增加全局 `safe_main` 兜底，任何崩溃都返回稳定 JSON
4. [x] debug 模式下实现保底输出：即使内部异常，仍输出可解析 JSON + fallback 字段
5. [x] 在 `SKILL.md` 补充 OpenClaw Heartbeat 推荐配置（`every: 10m`，内测 `target:none`）
6. [x] 在 `HEARTBEAT.md` 补充 deploy/debug 行为约束（保持文档极短）
7. [x] 同步新增单元/集成测试：模式切换、异常兜底、debug 日志落盘
8. [x] 先跑新增测试，再跑全量测试，确保测试全绿

## 迭代三：KickCat v1.1（/cat + 增量记忆 + 自动 compact）

1. [x] 增加 `/cat` 专项互动入口并映射为 reducer ops
2. [x] 增加主记忆增量同步请求：3 小时检查一次，由 LLM 执行相关摘要同步
3. [x] 增加同步游标机制（`updated_at + id`），避免全量回扫
4. [x] 增加内部记忆 compact 请求：每日 03:00 与 32KB 双触发，由 LLM 语义压缩
5. [x] 将同步与 compact 请求集成到 `tick`，异常时安全降级
6. [x] 扩展状态结构与 summary 输出（memory 元信息）
7. [x] 新增/更新单元与 CLI 测试覆盖 v1.1 行为
8. [x] 更新文档（`README.md`、`SKILL.md`、`HEARTBEAT.md`）
9. [x] 运行增量测试与全量测试，确保全部通过

## 迭代四：LLM 语义压缩分桶与轻学习（v1.1+）

1. [x] 将内部记忆拆分为任务相关与非任务相关分桶
2. [x] 分桶上限按 UTF-8 字节执行：任务 8KB、非任务 4KB
3. [x] `tick` 输出 memory_action 时附带分桶限额约束
4. [x] `apply memory_sync_upsert` 支持分桶写入与游标更新
5. [x] `apply memory_compact_replace` 支持分桶语义压缩回写
6. [x] 新增 `preference_upsert`，实现轻量偏好学习（白名单）
7. [x] 确保 `init`/仓库更新不清零本地内部记忆
8. [x] 更新文档（README/SKILL/HEARTBEAT）说明新约束与流程
9. [x] 新增与更新测试，覆盖分桶限额、学习机制、持久化行为
10. [x] 运行增量与全量测试，全部通过

## 迭代五：v1.2 极简化重构（生命周期 + 离散强度）

1. [x] 收敛活动状态模型为最小生命周期（`idle/active/cooldown`）并保证旧状态兼容
2. [x] 将 tick 漂移逻辑改为可复用内核函数，并引入活动离散强度对漂移速度的受控影响
3. [x] 将自由活动候选改为显式生命周期事件（`activity_start/activity_progress/activity_end`）且任务提醒优先
4. [x] 新增 `activity_plan_upsert` reducer，允许 LLM 以白名单活动类型 + 离散强度建议后续活动
5. [x] 同步更新 `summary` 与 `/cat` 输出，保持非 DEBUG 下不泄露数值细节
6. [x] 更新文档契约（`SKILL.md`、`README.md`、`HEARTBEAT.md`）以匹配极简化架构
7. [x] 新增/调整单元与 CLI 测试并跑全量，确保全绿

## 迭代六：v2.0 子能力库与受控学习激活（规划）

1. [ ] 新能力学习默认先进入 `pending`，仅做 shadow 命中统计，不直接生效
2. [ ] 定义并落地 `learning -> hit -> validate` 三段判定与结构化日志字段
3. [ ] 增加 `pending -> active` 激活阈值（命中次数、成功率、时间窗）
4. [ ] 增加 `active -> pending` 自动降级（连续失败/窗口失败率超阈值）
5. [ ] 将能力模板与运行态拆分为 `./data` 下模板文件 + runtime 文件（runtime gitignore）

## 迭代七：v1.3 稳定性与交互负担调优

1. [x] 心跳步长调整为 20 分钟（`TICK_MINUTES = 20`）
2. [x] 饥饿自然漂移调整为每 tick `+2`（保持活动强度系数）
3. [x] 投喂效果调整为 `-15..-30`（由 `feed_strength` 映射）
4. [x] 更新契约文档（`README.md`、`SKILL.md`、`HEARTBEAT.md`）与版本标识至 v1.3
5. [x] 更新并补充单元测试后跑全量测试，确保全绿
