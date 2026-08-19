# Agent Memory Guardrails

面向本地优先 AI 编程记忆的完整方案：自研引擎 + 治理规则 + 诊断 + 文件级档案，一个包。

> **状态：**早期 Alpha，尚未发布到 PyPI。
> **与 projectmem 的关系：**磁盘格式（`.projectmem/` 布局、六类事件、派生
> `summary.md`）与 [projectmem](https://github.com/riponcm/projectmem) 0.2.x 兼容，
> 存量记忆可无损沿用；但本项目不再依赖或包装它——引擎自研，根治其架构级限制
> （precheck 无索引、每条事件全量重写、捕获只认 cwd、只识别英文提交、无归档、无锁）。

[English](./README.md)

## 它解决什么问题

记忆引擎可以保存事件、生成摘要和暴露 MCP 工具，但稳定工程流程还需要：

- 明确区分不可手改的历史层与需要直接维护的当前地图/计划。
- 不默认开启尚未验证的 hook 与全局继承。
- 为 OpenCode、ZCode、Claude、Cursor、Codex 生成一致配置。
- 用 doctor 核实记忆根、隐私策略、hook、Agent 规则和潜在密钥泄漏。
- 真正的项目族模式：记忆锚定在多个仓库的父目录，向上发现、跨进程锁、
  父目录锚定的自动捕获都可用。
- 随记忆增长不退化的性能：索引化 precheck、增量再生、可逆归档。

## 快速使用

```bash
python -m pip install -e .

# 团队模式：可共享蒸馏文档，忽略原始事件与运行文件
amguard init D:\path\to\repo --profile team --client opencode

# 私有模式：忽略整个 .projectmem
amguard init D:\path\to\repo --profile private --client claude

# 项目族模式：记忆锚定在父目录（hook 也支持）
amguard init D:\work\repo-a --profile family --memory-root D:\work --client codex

# 审计
amguard doctor D:\path\to\repo
amguard doctor D:\path\to\repo --json
```

## 命令一览

```text
amguard log|attempt|fix|decision|note <text> [--at loc]   # 记录（语义与上游一致）
amguard show|regenerate|search|precheck|context           # 读取与维护
amguard archive --before DATE [--decisions-before DATE] [--restore]  # 生命周期（可逆）
amguard backup [--to DIR] [--verify ZIP]               # 全记忆快照（自校验）
amguard capture commit|merge                              # 双语自动捕获
amguard hooks install|uninstall                           # 自管 hook（运行时钉扎）
amguard dossier <path> [--validate]                       # 通用文件档案
amguard mcp                                               # 15 工具 MCP 服务器
```

## 三种策略

| 策略 | 记忆位置 | 适用场景 |
|---|---|---|
| `team` | 当前仓库 | 团队共享地图、计划和蒸馏知识 |
| `private` | 当前仓库 | 个人试用或记忆包含内部调查细节 |
| `family` | 祖先目录 | 同一产品下多个紧密关联仓库共享历史 |

`family` 模式的 hook 由 amguard 自有 hook 支持：从 Git 根向上发现记忆，父目录锚定可用。

## 默认安全边界

`amguard init` 保持自动化可选：Git hook 需 `--enable-hooks`（或 `amguard hooks install`）；
全局经验库自动晋升默认关闭（只读）。

## doctor 检查项

- 记忆根和必要文件。
- 团队/私有/项目族策略是否真实生效。
- `AGENTS.md` 工作流是否完整。
- 原始事件和运行文件是否被 Git 忽略。
- `events.jsonl` 是否被错误跟踪。
- `family` 策略下项目根与共享记忆根是否都安全。
- 遗留 projectmem hook 块（迁移提示）与 amguard hook 运行时漂移。
- Windows hook 路径与无法解析的运行时钉扎。
- OpenCode 配置中的 `--root` 是否指向真实记忆根。
- 常见凭据模式；报告只显示类型和行号，不回显值。

## 数据来源边界

`events.jsonl`、`summary.md`、`issues/*.md`、`archive/` 由工具维护，不手工修改
（`archive` 经 `amguard archive` 操作且可逆）。`PROJECT_MAP.md` 和 `plan.md`
分别记录当前结构与当前意图，需要直接维护。

## 开发验证

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m build
python -m twine check --strict dist/*
```

本项目使用 [MIT License](./LICENSE)。
