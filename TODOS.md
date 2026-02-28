# KickCat v1 TODOs

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
