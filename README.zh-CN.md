# Obsidian Skill Router

[![CI](https://github.com/xiademo/obsidian-skill-router/actions/workflows/ci.yml/badge.svg)](https://github.com/xiademo/obsidian-skill-router/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

简体中文 | [English](README.md)

`obsidian-skill-router` 是离线、本地优先的 Skill Registry Compiler 与任务路由器。它读取已经下载的本地 Skill 元数据，生成可由机器路由、也可在 Obsidian 中审计浏览的 Vault。

项目不会安装、下载、更新、发布或执行 Skill，也不会修改源 Skill。

> [!WARNING]
> 生成的 Vault 属于敏感元数据清单，其中包含本地源路径、Skill 名称与描述、时间和 hash。除非已经逐项审查并脱敏，否则不要公开或云同步。工具不会复制源 Skill 本体。

## 安装

需要 Python 3.10 或更高版本，无运行时第三方依赖。

推荐使用 `pipx` 隔离安装：

```bash
pipx install git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
obsidian-skill-router --version
```

也可使用 `uv`：

```bash
uv tool install git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
```

虚拟环境、开发安装、升级和卸载方法见[安装文档](docs/INSTALLATION.md)。

## 快速开始

```powershell
obsidian-skill-router scan `
  --skills-root "$HOME\.codex\skills" `
  --vault "$HOME\Documents\SkillRouterVault"

obsidian-skill-router validate `
  --vault "$HOME\Documents\SkillRouterVault" `
  --strict `
  --check-sources

obsidian-skill-router route `
  --vault "$HOME\Documents\SkillRouterVault" `
  --strategy hybrid `
  --task "审核并改进 Skill 路由规则"
```

机器输出使用全局 `--json` 参数，并放在子命令前：

```powershell
obsidian-skill-router --json route --vault .\vault --strategy hybrid --task "audit Skill routing"
```

## 核心设计

- `routing-index.json` schema `0.2` 是唯一机器真源；Markdown 和 CSV 是派生审计视图。
- 精确名称/alias、字段化稀疏检索、字符 n-gram、Package 软先验与 RRF 融合。
- manual、profile、compatibility、否定触发和执行风险在排序前门控。
- 支持 recommend、clarify、no-skill、review 和多 Skill plan。
- 图关系默认仅用于审计和 shadow 重排；candidate edge 不进入默认路由。
- LLM 只可生成离线提案；未经批准或 source hash 过期的提案不参与路由。
- Python Router 是唯一决策实现；Hook 只负责协议适配，绝不执行 Skill。

详细设计见[架构文档](docs/ARCHITECTURE.md)。

## 常用命令

```text
scan       扫描 Skill 元数据并生成 Vault
validate   校验 schema、source hash、链接、边和 managed files
report     读取生成报告
route      返回单 Skill 或多 Skill 路由决策
hook       读取一个 JSON 事件并返回 route-only 决策
eval       对多个路由策略运行离线评测
```

配置和治理 overlay 示例位于 [`examples/`](examples/)。测试与发布门禁见[测试计划](docs/TEST_PLAN.md)。

## 仓库边界

- `src/obsidian_skill_router/`：Python 本体。
- `tests/`：纯合成或脱敏 fixture 与自动测试。
- `SKILL.md`、`agents/`、`references/`：供代理使用本仓库的 companion Skill 元数据，不包含被路由的实体 Skill。
- `test_artifacts/`：本地 smoke scan 和生成 Vault；Git 忽略。
- 不扫描 `.agents/skills`，不增加服务、UI、MCP、网络路由、向量数据库或运行时 LLM。

## 治理与许可证

- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [版本记录](CHANGELOG.md)

项目使用 [MIT License](LICENSE)。
