# ci-context

> 一条命令获取完整的 CI 失败上下文。

**ci-context** 是一个 Python CLI 工具，给定失败的 GitHub Actions run ID，自动获取并综合所有相关上下文，生成可读的失败诊断报告——让你不再花 10–30 分钟手动翻日志、查 commits、找 PR 评论。

## 快速开始

```bash
# 安装
pip install ci-context

# 分析失败的 run
ci-context gh run 12345

# 当前仓库最近的失败
ci-context gh recent

# 指定仓库的最近失败
ci-context gh repo owner/repo
```

## 功能特性

- **错误提取引擎** — 自动从 CI 日志中提取实际错误（Python、Node.js、Go、Java、Shell 等）
- **Commit 上下文** — 展示触发 run 的 commit diff 摘要
- **PR 上下文** — 如果由 PR 触发，展示 review 状态和最新评论
- **历史模式匹配** — 检测最近 runs 中的重复错误（"这个错误在过去 30 天出现了 3 次"）
- **Rich 终端输出** — 彩色、结构化的终端报告
- **JSON 输出** — `--json` 标志供程序化消费
- **零 AI 依赖** — 确定性、快速、无需 AI API key（仅需 GitHub 认证）

## 系统要求

- Python 3.11+
- GitHub 认证（`gh auth login` 或 `GITHUB_TOKEN` 环境变量）

## 安装

```bash
# pip
pip install ci-context

# pipx（推荐 — 隔离环境）
pipx install ci-context
```

## 使用方法

### 分析特定 run

```bash
ci-context gh run 12345
```

### 当前仓库最近失败

```bash
cd your-repo
ci-context gh recent
```

### 指定仓库

```bash
ci-context gh repo owner/repo
```

### JSON 输出

```bash
ci-context gh run 12345 --json
```

## 许可证

MIT
