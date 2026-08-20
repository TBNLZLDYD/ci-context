# ci-context

> 一条命令获取完整的 CI 失败上下文。

**ci-context** 是一个 Python CLI 工具。给定一个失败的 GitHub Actions run
ID，它会自动抓取并综合所有相关上下文——错误、commit diff、PR 评论、历史
模式——并整合成一份可读的失败诊断报告。从此告别花 10-30 分钟手动翻
日志、查 commits、找 PR 评论。

整条流水线**确定性、零 AI、本地优先**：基于正则的错误提取器 + 稳定的
指纹匹配器 + SQLite 缓存，让每次运行既快又可审计。

## 快速开始

```bash
# 1. 安装（需要 Python 3.11+）
pip install ci-context
# 或者用隔离环境：
pipx install ci-context

# 2. 确保已执行 `gh auth login`，或通过 --token 传 token，
#    或写入配置文件（见下文「认证」一节）。

# 3. 分析一个失败的 run
ci-context gh run 12345 --repo owner/repo

# 4. 在带 GitHub remote 的 git 仓库内，可省略 --repo
cd your-repo
ci-context gh run 12345
```

整个工作流就是一条命令：把 run ID 变成一份六段式报告——Run Overview、
Extracted Errors、Commit Context、PR Context、History Pattern、Quick
Actions。

## 功能特性

- **认证** — `--token` 参数、配置文件、`gh auth login` 三选一，按优先级自动选取。
- **错误提取** — 基于正则的尾优先扫描，跨失败 job 去重，每条错误带置信度
  等级（high / medium / low）和来源 step，每 run 上限 10 条。
- **Commit 上下文** — 触发 SHA、commit message、作者、变更文件列表（含
  `+/-` 行数）和依赖变更检测。
- **PR 上下文** — 标题、编号、作者、状态、review 结论、最新 review 评论
  及正文摘要（仅 JSON 输出；仅在 run 由 `pull_request*` 触发时出现）。
- **历史模式匹配** — 将每条错误在最近 N 次同 workflow 的 run 中分类为
  `[exact]` / `[similar]` / `[new]`；展示失败率趋势；若多次复发共享
  同一模式，给出 commit pattern 提示。
- **指纹缓存** — 复现性分析走 7 天 SQLite 缓存（`~/.cache/ci-context/history.db`）短路。
- **可靠性** — 自动指数退避重试、速率限制预检并给出友好错误、10 秒网络
  超时、超大日志截断（头尾窗口 + `... (skipped N lines) ...` 标记）。
- **两种输出格式** — Rich 彩色终端报告（默认）与符合 F6 schema 的 JSON（`--json`）。

## 命令参考

### `ci-context gh run <run-id>`

分析单个 GitHub Actions run 并输出失败报告。

| 选项 | 短选项 | 默认值 | 描述 |
|------|--------|--------|------|
| `--repo` | `-r` | 从 `git remote` 推断 | 仓库标识，格式 `owner/repo`。 |
| `--attempt` | | 最新 | 要分析的 attempt 编号。 |
| `--force` | | `false` | 分析非失败 run（成功、进行中、已取消等）。 |
| `--no-history` | | `false` | 跳过历史模式匹配。 |
| `--no-pr` | | `false` | 跳过 PR 上下文获取。 |
| `--max-history` | | `30` | 扫描的历史 run 数量。 |
| `--error-lines` | | `5` | 每个错误展示的原始日志行数（上限 5——提取器最多保留 5 行）。 |
| `--json` | `-j` | `false` | 输出符合 F6 schema 的 JSON。 |
| `--no-color` | | `false` | 禁用 ANSI 彩色输出。 |
| `--token` | | 配置文件 / `gh auth` | GitHub API token。 |

```bash
# 标准用法
ci-context gh run 12345 --repo owner/repo

# 输出 JSON 以便管道到 jq
ci-context gh run 12345 --repo owner/repo --json | jq '.errors'

# 跳过慢速的上下文获取
ci-context gh run 12345 --no-history --no-pr

# 检查还在跑着的 run
ci-context gh run 12345 --force
```

### `ci-context gh recent`

展示当前仓库最近的失败 run。优先使用 `--repo` 指定仓库，否则从当前
`git remote` 推断。除列出失败 run 外，还会输出整体 vs. 近期失败率
以及趋势（`increasing` / `stable` / `decreasing`）。

| 选项 | 短选项 | 默认值 | 描述 |
|------|--------|--------|------|
| `--repo` | `-r` | 从 `git remote` 推断 | 仓库标识，格式 `owner/repo`。 |
| `--limit` | | `10` | 展示的最近失败 run 数量。 |
| `--json` | `-j` | `false` | 输出 JSON。 |
| `--no-color` | | `false` | 禁用 ANSI 彩色输出。 |
| `--token` | | 配置文件 / `gh auth` | GitHub API token。 |

```bash
ci-context gh recent
ci-context gh recent --repo owner/repo --limit 20
```

### `ci-context gh repo <owner/repo>`

为指定仓库展示最近失败 run。输出与 `gh recent` 一致，只是把仓库作为
位置参数传入（而非从当前工作目录推断）。

| 选项 | 短选项 | 默认值 | 描述 |
|------|--------|--------|------|
| `--limit` | | `10` | 展示的最近失败 run 数量。 |
| `--json` | `-j` | `false` | 输出 JSON。 |
| `--no-color` | | `false` | 禁用 ANSI 彩色输出。 |
| `--token` | | 配置文件 / `gh auth` | GitHub API token。 |

```bash
ci-context gh repo owner/repo
ci-context gh repo owner/repo --limit 25 --json
```

### `ci-context cache …`

管理本地指纹 / run 元数据缓存。

| 命令 | 描述 |
|------|------|
| `cache clear` | 清空所有缓存表。 |
| `cache stats` | 展示行数、数据库大小、数据库路径。 |
| `cache purge` | 仅删除 TTL 过期行（超过 7 天的）。 |

```bash
ci-context cache stats
ci-context cache purge
ci-context cache clear
```

### 全局选项

| 选项 | 短选项 | 描述 |
|------|--------|------|
| `--version` | | 输出版本号并退出。 |
| `--verbose` | `-v` | 启用 `DEBUG` 级日志（错误时同时打印堆栈）。 |
| `--help` | `-h` | 显示任意命令的帮助。 |

## 认证

按以下优先级解析 token——首个命中即用。

1. **`--token` 参数** — `ci-context gh run 12345 --token ghp_xxx`
2. **配置文件**，路径二选一：
   - Linux / macOS：`$XDG_CONFIG_HOME/ci-context/config.toml` 或
     `~/.config/ci-context/config.toml`
   - Windows：`%APPDATA%\ci-context\config.toml`

   文件格式（TOML）：

   ```toml
   token = "ghp_xxxxxxxxxxxxxxxxxxxx"
   ```

3. **`gh auth token`** — 调用 GitHub CLI 自身的 token。要求本机已安装
   并认证过 `gh`。

若以上三种方式都拿不到 token，命令会以 `AuthError` 退出，错误信息会
列出所有尝试过的方法。

## 报告结构

`ci-context gh run <id>` 组装一份 `FailureReport`，并以六段式（终端）或
单个 JSON 对象（`--json`）形式渲染。

| # | 段落 | 内容 |
|---|------|------|
| 1 | **Run Overview** | Run ID、workflow、结论、触发事件、head SHA、耗时、attempt、URL。 |
| 2 | **Extracted Errors** | 最多 10 条错误，含类型、消息、文件位置、置信度、来源 step 及几行原始日志作为上下文。 |
| 3 | **Commit Context** | 触发 commit 的 message、作者、时间戳、变更文件列表（含 `+/-` 行数）；涉及依赖变更时给警告标记。 |
| 4 | **PR Context** | 编号、标题、作者、状态、review 结论、最新 review 评论、正文摘要（仅 JSON）。仅在 `pull_request` / `pull_request_target` 触发的 run 中出现。 |
| 5 | **History Pattern** | 每条错误在最近 N 次 run 中的 `[exact]` / `[similar]` / `[new]` 分类；含整体 vs. 近期失败率与趋势。 |
| 6 | **Quick Actions** | 后续可直接粘贴的 `gh` / `git` 命令：查看完整日志、查看 commit、查看 PR、重跑失败 jobs。 |

通过 `--no-pr` 与 `--no-history` 按需跳过较慢的段落；通过
`--max-history` 调整历史窗口大小。

## 系统要求

- **Python 3.11+**（用到 `tomllib`、`match` 语句与现代类型注解）。
- 一枚能访问目标仓库的 **GitHub API token**——通过上述三种认证方式之一获取。
- 能访问 `api.github.com` 的网络。

## 安装

### 从 PyPI

```bash
pip install ci-context
# 推荐使用隔离环境：
pipx install ci-context
```

### 从源码（开发）

项目使用 **uv** 管理依赖。

```bash
git clone https://github.com/TBNLZLDYD/ci-context
cd ci-context
uv sync --dev
uv run ci-context --version
```

运行测试：

```bash
uv run pytest
```

## 数据 & 缓存路径

| 资源 | Linux / macOS | Windows |
|------|---------------|---------|
| 缓存数据库 | `~/.cache/ci-context/history.db`（尊重 `$XDG_CACHE_HOME`） | `%LOCALAPPDATA%\ci-context\history.db` |
| 配置文件 | `~/.config/ci-context/config.toml`（尊重 `$XDG_CONFIG_HOME`） | `%APPDATA%\ci-context\config.toml` |
| 缓存 TTL | 7 天（懒过期） | 7 天（懒过期） |

缓存中只保存错误指纹与 run 元数据——不会写入原始日志，也不会写入 token。

## 许可证

MIT
