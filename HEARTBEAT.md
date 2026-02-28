# KickCat Heartbeat

1. 每 20 分钟执行：`python3 skills/kickcat/kickcat.py tick`
2. 执行：`python3 skills/kickcat/kickcat.py summary`
3. 若 `candidate` 为 `none`，返回 `HEARTBEAT_OK`
4. 若 `candidate` 非 `none`，只输出一条简短消息
5. 若 `candidate` 为 `activity_start/activity_progress/activity_end`，优先使用 `tick` 返回的 `activity_hint` 输出猫咪活动短句
6. 30 分钟内避免重复同类提醒
7. 若检测到用户低落，优先温和语气
8. 线上默认 `deploy` 模式，排障时改 `debug` 模式
9. debug 模式下即使脚本异常，也要返回可解析 JSON（含 fallback）
10. 每次 tick 读取 `memory_action`：若请求同步/压缩，交给 LLM 执行语义流程
11. KickCat 不直接读主记忆；仅接收 LLM 结果并通过 apply 落盘
12. 语义压缩上限：任务相关 8KB，非任务相关 4KB（UTF-8 字节）
13. 技能更新时保留本地 KickCat 状态，不清零内部记忆
