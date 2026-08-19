# Engine cards

## `pollux/src/pollux/engine/commands.py`

- 状态：active
- 等级：A
- 作用：Memory 类——所有写操作（log/attempt/fix/decision/note）的锁内 读-追加-再生 完整序列，CLI 与 MCP 共用。
- 入口：`Memory`、`Memory.discover`、`Memory.write_lock`。
- 上游：`engine.storage`、`engine.summary`、`engine.locking`。
- 下游：`cli.py`、`engine.mcp_server`(M6)、`engine.capture`。
- 不变量：写锁覆盖 读→编号分配→追加→summary再生→marker更新 全程；任何写操作不得绕过锁。
- 验证：`python -m unittest discover -s tests -p "test_engine_commands.py"`。
- 权威文档：docs/architecture.md。
- 核实：committed baseline。

## `pollux/src/pollux/engine/summary.py`

- 状态：active
- 等级：A
- 作用：summary.md 与 issues/*.md 的派生再生；输出与历史引擎逐字节兼容（默认），但增量同步+孤儿清理+CJK slug+原子写。
- 入口：`build_summary`、`regenerate_summary`、`sync_issue_files`、`slugify`。
- 上游：`engine.models`、`engine.storage`、`files.write_text_atomic`。
- 下游：`engine.commands`、`engine.archive`、`engine.capture`。
- 不变量：recent_issues_limit=0 时输出与上游 build_summary 逐字节一致（黄金文件测试锁定）；摘要写入用平台默认行尾（Windows=CRLF）与上游一致。
- 验证：`python -m unittest discover -s tests -p "test_engine_summary.py"`。
- 权威文档：docs/architecture.md。
- 核实：committed baseline。
