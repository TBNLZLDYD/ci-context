# ci-context

> 一条命令获取完整的 CI 失败上下文。

**ci-context** 是一个 Python CLI 工具，给定失败的 GitHub Actions run ID，自动获取并综合所有相关上下文，生成可读的失败诊断报告 -- 让你不再花 10-30 分钟手动翻日志、查 commits、找 PR 评论。

**状态：v0.1.0b1 (PoC)** -- 核心数据获取管道已可用；错误提取、commit/PR 上下文、历史匹配、结构化渲染正在开发中。详见 [DDL](docs/DDL.md) 和 [架构图](docs/architecture.html)。

## 快速开始

```bash
# 安装
pip install ci-context

# 分析失败的 run（核心命令，已可用）
ci-context gh run 12345 --repo owner/repo

# 当前仓库最近的失败（即将支持）
ci-context gh recent

# 指定仓库的最近失败（即将支持）
ci-context gh repo owner/repo
```

## 当前可用功能

- **认证** -- `gh auth login`、配置文件、或 `--token` 参数
- **Run 信息** -- 获取 workflow run 详情（状态、结论、SHA、事件、耗时）
- **失败 Jobs** -- 列出失败 jobs 及 step 级别详情
- **日志获取** -- 下载 job 日志，大日志自动截断
- **日志规范化** -- 剥离 ANSI 码、GHA 时间戳、section/group 标记
- **PoC 报告** -- 内联 Rich 输出，展示 run 概览 + 规范化日志尾部

## 即将支持

- **错误提取** -- 基于 regex 的 Python/Node/Go/Java/Shell 错误提取
- **Commit 上下文** -- 触发 commit 的 diff 摘要
- **PR 上下文** -- Review 状态和最新评论
- **历史模式匹配** -- "这个错误在过去 30 天出现了 3 次"
- **Rich 结构化报告** -- 完整 PRD 格式报告，包含所有上下文区块
- **JSON 输出** -- `--json` 标志供程序化消费
- **SQLite 缓存** -- 减少重复运行时的 API 调用

## 系统要求

- Python 3.11+
- GitHub 认证（`gh auth login` 或 `--token` 参数）

## 安装

```bash
# pip
pip install ci-context

# pipx（推荐 -- 隔离环境）
pipx install ci-context
```

## 使用方法

### 分析特定 run

```bash
ci-context gh run 12345 --repo owner/repo
```

### 自动检测仓库

```bash
cd your-repo
ci-context gh run 12345
```

### 选项

| 选项 | 短选项 | 描述 |
|------|--------|------|
| `--repo` | `-r` | 仓库 (owner/repo)。从 git remote 自动推断。 |
| `--force` | | 分析非失败 run |
| `--verbose` | `-v` | 详细输出（DEBUG 级别日志） |
| `--token` | | GitHub API token |
| `--json` | `-j` | JSON 输出（即将支持） |
| `--no-color` | | 禁用彩色输出（即将支持） |
| `--no-history` | | 跳过历史模式匹配（即将支持） |
| `--no-pr` | | 跳过 PR 上下文获取（即将支持） |
| `--max-history` | | 历史运行数量（默认：30） |
| `--error-lines` | | 每个错误的原始日志行数（默认：5） |
| `--attempt` | | Attempt 编号（默认：最新） |

## 许可证

MIT
