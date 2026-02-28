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
  "reply_style": "gentle"
}
```

约束：模型只负责判断和提议，不直接写状态；最终结算必须调用 `kickcat.py apply`。

## reducer 映射

- `interaction_quality` -> `pet_delta(happiness, +1~+4)`
- `boredom_relief` -> `pet_delta(boredom, -1~-6)`
- `feed_strength` -> `pet_delta(hunger, feed_strength=0.0~1.0)`（脚本映射为 `-8~-15`）
- `task_candidates[]` -> `task_add`
- `mood_candidate` -> `mood_add`
- 只要发生有效对话 -> `touch_interaction`

## apply 调用顺序

1. 解析用户消息为结构化 JSON
2. 把结构化字段映射为 reducer ops
3. 调用 `python3 skills/kickcat/kickcat.py apply --payload ...`
4. 再调用 `summary` 或读取 apply 返回，生成简短回复

## OpenClaw 调用建议（社区实践）

- Heartbeat 配置建议：`every: "10m"`，内测期 `target: "none"`，稳定后切到 `target: "last"`
- 心跳主流程：`tick -> summary -> HEARTBEAT_OK/单条短消息`
- 脚本运行模式：
  - `deploy`（默认）：面向生产，错误输出最小化
  - `debug`：即使异常也保底输出 JSON，并写入 debug 日志
- 示例命令：
  - `python3 skills/kickcat/kickcat.py tick --mode deploy`
  - `python3 skills/kickcat/kickcat.py apply --payload ... --mode debug --debug-log-file logs/kickcat-debug.jsonl`

## 回复风格限制

- 轻人格化，不夸张角色扮演
- 一次只说一小段
- 提醒优先于卖萌
- 用户低落时改用温和语气，减少打扰
