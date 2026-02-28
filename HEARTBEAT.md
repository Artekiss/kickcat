# KickCat Heartbeat

1. 执行：`python3 skills/kickcat/kickcat.py tick`
2. 执行：`python3 skills/kickcat/kickcat.py summary`
3. 若 `candidate` 为 `none`，返回 `HEARTBEAT_OK`
4. 若 `candidate` 非 `none`，只输出一条简短消息
5. 30 分钟内避免重复同类提醒
6. 若检测到用户低落，优先温和语气
7. 线上默认 `deploy` 模式，排障时改 `debug` 模式
8. debug 模式下即使脚本异常，也要返回可解析 JSON（含 fallback）
