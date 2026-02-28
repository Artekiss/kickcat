---
name: cat
user-invocable: true
---

# KickCat Skill

## 名称与用途

KickCat 是轻量电子宠物提醒 Skill：维护宠物状态、接受闲聊/投喂/提醒/心情输入，并通过本地脚本完成状态结算。

## 触发条件

当用户消息包含以下任一意图时触发：

- 闲聊（chat）
- 投喂（feed）
- 顺手记录提醒事项（task_capture）
- 表达心情（mood_capture）
- heartbeat 轮询状态与候选动作
- 显式命令 `/cat <自然语言输入>`（强制进入 KickCat 专项互动）

## 结构化解析规范

模型优先输出结构化 JSON，再生成文本回复。建议格式：

```json
{
  "intents": ["chat", "feed", "task_capture", "mood_capture"],
  "interaction_quality": 7,
  "boredom_relief": 6,
  "feed_strength": 0.8,
  "task_candidates": [
    {
      "title": "finish structural report",
      "due_at": "2026-02-28T18:00:00Z",
      "requested_remind_at": "2026-02-28T17:30:00Z"
    }
  ],
  "mood_candidate": {
    "label": "stressed",
    "valence": -0.6,
    "intensity": 0.7
  },
  "activity_suggestion": {
    "kind": "play",
    "intensity_level": "mid",
    "duration_minutes": 20
  },
  "reply_style": "gentle"
}
```

约束：模型只负责判断和提议，不直接写状态；最终结算必须调用 `kickcat.py apply`。

## reducer 映射

- `interaction_quality` -> `pet_delta(happiness, +1~+4)`
- `boredom_relief` -> `pet_delta(boredom, -1~-6)`
- `feed_strength` -> `pet_delta(hunger, feed_strength=0.0~1.0)`（脚本映射为 `-15~-30`）
- `task_candidates[]` -> `task_add`
- `mood_candidate` -> `mood_add`
- `activity_suggestion` -> `activity_plan_upsert`（白名单 kind + 离散强度 `low|mid|high`）
- 只要发生有效对话 -> `touch_interaction`

## 活动生命周期（极简）

- 内核只维护三态：`idle -> active -> cooldown`
- heartbeat 可看到显式事件：`activity_start`、`activity_progress`、`activity_end`
- 强提醒（任务到期/高饥饿高无聊）优先，必要时中断活动
- 活动强度仅允许离散档位 `low|mid|high`，由脚本做边界约束

## apply 调用顺序

1. 解析用户消息为结构化 JSON
2. 把结构化字段映射为 reducer ops
3. 调用 `python3 skills/kickcat/kickcat.py apply --payload ...`
4. 再调用 `summary` 或读取 apply 返回，生成简短回复

## OpenClaw 调用建议（社区实践）

- Heartbeat 配置建议：`every: "20m"`，内测期 `target: "none"`，稳定后切到 `target: "last"`
- 心跳主流程：`tick -> summary -> HEARTBEAT_OK/单条短消息`
- `/cat` 路由建议：收到 `/cat <自然语言输入>` 后，先做意图结构化解析，再映射 reducer ops 并调用 `apply`
- 脚本运行模式：
  - `deploy`（默认）：面向生产，错误输出最小化
  - `debug`：即使异常也保底输出 JSON，并写入 debug 日志
- 示例命令：
  - `python3 skills/kickcat/kickcat.py tick --mode deploy`
  - `python3 skills/kickcat/kickcat.py apply --payload ... --mode debug --debug-log-file logs/kickcat-debug.jsonl`
  - `python3 skills/kickcat/kickcat.py cat --text "feed cat and remind me to finish report"`

## 主记忆同步（v1.2）

- 每 3 小时由 tick 产生 `request_memory_sync` 候选
- KickCat 脚本不直接读取或规范化主记忆
- 由 OpenClaw/LLM 执行增量语义同步，再调用 `apply` 写入 `memory_sync_upsert`
- 无关内容由 LLM 判断并跳过，同步游标建议使用 `updated_at + id`
- 同步写入分桶：`task_related_items` 与 `non_task_related_items`

## 内部记忆 compact（v1.2）

- 当地时间每日 03:00 后，或内部记忆达到 32KB 时，tick 产生 `request_memory_compact`
- 由 LLM 返回语义压缩结果，再调用 `apply` 的 `memory_compact_replace`
- 大小上限（UTF-8 字节）：`task_related <= 8KB`，`non_task_related <= 4KB`
- KickCat 仅做结构校验、大小约束与落盘

## 轻学习机制（v1.2+）

- 允许 LLM 通过 `preference_upsert` 更新轻量偏好（如语气、互动密度）
- 仅白名单字段可写入，避免过度预定义和无边界膨胀
- 用于逐步贴合用户，不做复杂画像系统

## 持久化与部署约束

- Skills 代码更新不应清零 KickCat 本地内部记忆
- `init` 仅补齐 schema，不覆盖已有状态

## 回复风格限制

- 轻人格化，不夸张角色扮演
- 一次只说一小段
- 提醒优先于卖萌
- 用户低落时改用温和语气，减少打扰
- 严格调试门控：仅当用户输入明确为 `/cat DEBUG ...`（不区分大小写）时，允许输出具体状态数值或调试信息
- 若未命中 `/cat DEBUG ...`，禁止输出 `hunger/happiness/boredom` 数值、memory bytes、ops 计数、阈值、内部 reason code、trace/debug 字段
- 非紧急状态下可输出一条猫咪活动提示，但当存在任务提醒或强提醒时必须让位
