# ci-context — 产品需求文档 (PRD)

> **版本：** 0.1-beta PRD v1.0
> **日期：** 2026-07-16
> **作者：** 基于头脑风暴报告 + 竞品调研撰写
> **状态：** Draft — 待确认后进入开发

---

## 目录

1. [产品概述](#1-产品概述)
2. [市场与竞品分析](#2-市场与竞品分析)
3. [目标用户](#3-目标用户)
4. [核心价值主张](#4-核心价值主张)
5. [功能规格](#5-功能规格)
6. [CLI 命令设计](#6-cli-命令设计)
7. [输出格式设计](#7-输出格式设计)
8. [技术架构](#8-技术架构)
9. [错误提取引擎](#9-错误提取引擎)
10. [历史模式匹配](#10-历史模式匹配)
11. [数据流与 API 调用](#11-数据流与-api-调用)
12. [0.1-beta 范围与里程碑](#12-01-beta-范围与里程碑)
13. [非功能性需求](#13-非功能性需求)
14. [风险与缓解](#14-风险与缓解)
15. [成功指标](#15-成功指标)
16. [未来路线图（0.2+）](#16-未来路线图02)
17. [附录](#17-附录)

---

## 1. 产品概述

### 1.1 一句话描述

**ci-context** 是一个 Python CLI 工具，给定失败的 GitHub Actions run ID，一条命令自动获取并综合所有相关上下文，生成可读的失败诊断报告——让你不再花 10-30 分钟手动翻日志、查 commits、找 PR 评论。

### 1.2 问题陈述

当 CI 管道失败时，开发者需要手动执行以下步骤来理解"为什么失败了"：

| 步骤 | 当前操作 | 耗时 |
|------|----------|------|
| 1. 找到失败的 run | 打开 GitHub → Actions tab → 找到红色 ❌ | 1-2 min |
| 2. 查看哪个 job/step 失败 | 点击 run → 展开 jobs → 找到红色 step | 1-2 min |
| 3. 阅读日志找错误 | 点击 step → 在数千行日志中找 error/traceback | 5-15 min |
| 4. 查看触发 commit | 切到 commit 页 → 看 diff | 2-3 min |
| 5. 检查相关 PR | 切到 PR 页 → 看 review 评论 | 2-3 min |
| 6. 检查历史模式 | 手动翻最近 runs → 这个错误以前出现过吗？ | 3-5 min |
| **合计** | | **15-30 min** |

**ci-context 将这 6 步压缩为 1 条命令，耗时 < 10 秒。**

### 1.3 产品定位

| 维度 | 定位 |
|------|------|
| **品类** | CI/CD 开发者工具 — 失败上下文综合器 |
| **差异化** | 确定性、本地优先、零 AI 依赖、一条命令 |
| **不是什么** | 不是 AI 调试器（不生成修复建议）、不是 CI 运行器（不替代 `act`）、不是日志搜索引擎（不替代 `grep`） |

---

## 2. 市场与竞品分析

### 2.1 竞品矩阵

| 工具 | 类型 | 需要 AI/API Key | 做什么 | 不做什么 | ci-context 差异化 |
|------|------|:---:|------|------|------|
| **`gh run view --log-failed`** | GitHub CLI 内置 | ❌ | 显示失败 job 的原始日志 | 不综合上下文、不提取错误、不匹配历史、不关联 PR | ci-context **综合**多源信息，而非只展示原始日志 |
| **gha-failure-analysis** (scalebevans) | GitHub Action | ✅ (OpenAI/Anthropic/Gemini/Ollama) | LLM 分析失败日志 + PR 上下文关联 | 需要 LLM API key、只在 CI 内运行（非本地 CLI）、每次调用消耗 token | ci-context **确定性 + 本地 CLI**，无需 AI key，开发者主动调用 |
| **sensethelog/action** | GitHub Action | ✅ (AI) | AI 驱动的根因分析 + 修复建议 | 同上：CI-only、需 AI key | 同上 |
| **Build Failure Analyser** (Superbasil3) | GitHub Action | ❌ | 用用户自定义 regex 匹配日志中的已知失败模式 | 需手动维护 regex 列表、不综合上下文、不关联 commit/PR | ci-context **内置**多语言错误提取 + 自动关联 commit/PR/历史 |
| **GitHub Copilot CLI** | 终端 AI Agent | ✅ (Copilot 订阅) | 通用终端 AI 助手，可调试 CI | 通用工具非专用、需订阅、非确定性、慢 | ci-context **专用 + 快速 + 确定性**——不需要等 AI 思考 |
| **OpenAI Codex autofix Action** | GitHub Action | ✅ (OpenAI API) | CI 失败时自动生成修复 PR | 完全自动（开发者不参与诊断）、需 OpenAI key | ci-context 帮开发者**理解**失败，而非自动修复 |

### 2.2 市场空白确认

**核心空白：没有工具同时满足以下三个条件——**

1. ✅ **确定性**（不需要 AI API key，不消耗 token，结果可复现）
2. ✅ **本地 CLI**（开发者主动在终端调用，不是 CI 内自动运行）
3. ✅ **多源综合**（日志错误提取 + commit diff + PR 上下文 + 历史模式，一个报告）

现有工具要么是 AI 驱动的（需要 key + 慢 + 贵），要么只做单点（只看日志 / 只匹配 regex），要么是 CI-only（不是开发者本地工具）。

### 2.3 与 GitHub Copilot CLI 的共存策略

GitHub Copilot CLI（2026-02 GA）是通用 AI 终端助手，可以"问它 CI 为什么失败"。但：

| 维度 | Copilot CLI | ci-context |
|------|-------------|------------|
| **速度** | 5-30 秒（LLM 推理） | < 3 秒（纯 API 调用 + 本地处理） |
| **成本** | 消耗 Copilot 配额/token | 免费（仅 GitHub API 速率限制） |
| **确定性** | 每次可能不同 | 完全确定性，可复现 |
| **离线** | 需要网络 + AI 服务 | 只需 GitHub API（已通过 `gh auth` 认证） |
| **专用性** | 通用助手 | CI 失败专用——更精准的上下文选择 |

**定位话术：** "Copilot 帮你想答案，ci-context 帮你找事实。先用 ci-context 10 秒拿到所有上下文，再决定是否需要 AI 深入分析。"

---

## 3. 目标用户

### 3.1 主要用户画像

**画像 A：日常 CI 用户（核心用户）**
- 每天触发 5-20 次 GitHub Actions
- 收到 CI 失败通知后，第一反应是打开浏览器翻日志
- 痛点：频繁的上下文切换（终端 → 浏览器 → 终端），日志太长找不到关键行
- 期望：一条命令在终端内搞懂失败原因

**画像 B：多仓库维护者**
- 维护 3-10 个活跃仓库
- 痛点：每天要检查多个仓库的 CI 状态，手动收集上下文耗时累加
- 期望：`ci-context gh repo owner/repo` 快速看到最近失败摘要

**画像 C：开源项目贡献者**
- 提交 PR 后等 CI 结果
- 痛点：CI 失败后不知道是自己代码的问题还是环境问题
- 期望：快速看到失败上下文 + 历史模式（"这个错误上周也出现过"）

### 3.2 非目标用户

- 不使用 GitHub Actions 的团队（0.1 不支持 GitLab/CircleCI/Jenkins）
- 需要 AI 自动修复的用户（ci-context 只诊断，不修复）
- 企业级 CI 可观测性需求（需要 Dashboard、告警、SLA 追踪——这是 Datadog/CircleCI 的领域）

---

## 4. 核心价值主张

### 4.1 价值公式

```
ci-context 价值 = (手动收集上下文耗时 - ci-context 运行耗时) × 每日 CI 失败次数 × 使用天数
                = (15 min - 0.2 min) × 3 次/天 × 250 工作日
                = ~187 小时/年 节省
```

### 4.2 三个核心价值

1. **速度**：10 秒内出报告，vs 手动 15-30 分钟
2. **综合**：6 种上下文源（日志错误、commit diff、PR 评论、历史模式、workflow 信息、job 详情）汇入一个报告
3. **确定性**：不需要 AI key，不需要等 LLM 推理，结果可复现

### 4.3 "Aha Moment" 定义

用户第一次运行 `ci-context gh run 12345`，看到报告中的 **"历史模式匹配"** 部分——"这个错误在过去 30 次运行中出现了 3 次，都与依赖 `requests` 更新相关"——这一刻用户意识到这不是简单的日志查看器，而是有智能上下文的诊断工具。

---

## 5. 功能规格

### 5.1 功能全景与优先级

| # | 功能 | 优先级 | 0.1-beta | 描述 |
|---|------|:---:|:---:|------|
| F1 | `ci-context gh run <run-id>` | P0 | ✅ | 核心命令：获取失败 run 并生成综合报告 |
| F2 | 错误提取引擎 | P0 | ✅ | 从 CI 日志中自动提取实际错误行 |
| F3 | Commit 上下文 | P0 | ✅ | 展示触发 run 的 commit diff 摘要 |
| F4 | PR 上下文 | P0 | ✅ | 如果 run 由 PR 触发，展示 PR 评论和 review 状态 |
| F5 | 历史模式匹配 | P0 | ✅ | 检查最近 N 次 runs 的相似错误模式 |
| F6 | `--json` 输出 | P1 | ✅ | JSON 格式输出，供程序化消费 |
| F7 | `ci-context gh repo <owner/repo>` | P1 | ✅ | 仓库最近失败摘要 |
| F8 | `ci-context gh recent` | P1 | ✅ | 当前仓库最近失败的 runs 列表 |
| F9 | Rich 终端输出 | P0 | ✅ | 彩色、结构化的终端报告 |
| F10 | `--markdown` 输出 | P2 | ❌ | Markdown 文件输出（0.2） |
| F11 | GitHub Action 集成 | P2 | ❌ | 作为 Action 在 CI 内运行（0.2） |
| F12 | GitLab CI 支持 | P3 | ❌ | 扩展到 GitLab（0.3+） |
| F13 | 插件系统 | P3 | ❌ | 自定义错误提取器（0.3+） |
| F14 | 本地失败历史缓存 | P2 | ✅(简化) | SQLite 缓存历史 runs 加速模式匹配 |

### 5.2 功能详细规格

#### F1: `ci-context gh run <run-id>`

**输入：**
- `run-id`（必需）：GitHub Actions run ID（数字）
- `--repo` / `-r`（可选）：仓库标识 `owner/repo`，默认从当前 git remote 推断
- `--attempt`（可选）：指定 attempt 编号（默认最新）

**处理流程：**
1. 验证 `gh auth` 状态 → 未认证则提示 `gh auth login`
2. 获取 run 详情（status、conclusion、head_sha、event、workflow name）
3. 如果 run 状态不是 `failure`，提示并退出（`--force` 可强制分析非失败 run）
4. 获取失败 jobs 列表
5. 对每个失败 job：获取日志 → 运行错误提取引擎
6. 获取触发 commit 的 diff 摘要
7. 如果是 PR 触发：获取 PR 评论和 review
8. 获取同 workflow 最近 30 次 runs → 运行历史模式匹配
9. 综合所有上下文 → 渲染报告

**输出：** 终端 Rich 报告（默认）或 JSON（`--json`）

**错误处理：**
- run-id 不存在 → "Run 12345 not found in owner/repo"
- run 成功 → "Run 12345 completed successfully. Use --force to analyze anyway."
- API 速率限制 → "GitHub API rate limit hit. Retry after HH:MM UTC. Tip: use GITHUB_TOKEN for higher limits."
- 日志获取失败（已知 gh CLI bug）→ 降级：展示 job/step 信息但不展示提取的错误，提示 "Could not fetch logs (known GitHub API issue). View at: <url>"

#### F2: 错误提取引擎

**设计原则：** 不尝试"理解"日志，只做**结构化提取**——用 regex 启发式识别已知错误模式，提取关键行。

**支持的错误模式（0.1-beta）：**

| 语言/框架 | 错误模式 | 提取内容 |
|-----------|----------|----------|
| **Python** | `Traceback (most recent call last):` → `ErrorType: message` | 异常类型 + 消息 + 文件:行号 |
| **Python** | `FAILED test_file.py::test_name` | 失败测试名 + 断言消息 |
| **Python** | `ModuleNotFoundError: No module named 'X'` | 缺失模块名 |
| **Python** | `ImportError: ...` | 导入错误详情 |
| **Node.js** | `Error: message` + `at function (file:line:col)` | 错误消息 + 堆栈首行 |
| **Node.js** | `FAIL test_name` (Jest) | 失败测试名 + 错误消息 |
| **Node.js** | `npm ERR! ...` | npm 错误详情 |
| **Go** | `panic: message` + `goroutine N [running]:` | panic 消息 + goroutine |
| **Go** | `FAIL\tpackage_name\t[duration]` | 失败包名 |
| **Java** | `Exception in thread "main" Type: message` | 异常类型 + 消息 |
| **Java** | `Tests run: N, Failures: M, Errors: K` | 测试统计 |
| **Shell** | `Error: message` / `error: message` | 错误消息 |
| **Shell** | `command not found: X` | 缺失命令 |
| **Shell** | `permission denied: X` | 权限错误 |
| **Docker** | `ERROR: ...` / `failed to fetch ...` | 构建错误 |
| **通用** | `fatal: ...` | Git/通用致命错误 |
| **通用** | `Process completed with exit code N` | 退出码（GitHub Actions 特有） |

**提取策略：**
1. 从日志末尾向前扫描（错误通常在日志尾部）
2. 对每种模式，提取：错误类型、消息、文件位置（如有）
3. 去重：同一错误在多个 step 出现只报告一次，标注出现次数
4. 置信度标注：`[high]` 精确匹配（如 Python Traceback）、`[medium]` 模式匹配（如 `Error: ...`）、`[low]` 仅退出码

**输出结构：**
```python
@dataclass
class ExtractedError:
    error_type: str          # "Python Traceback", "npm Error", "Go panic", etc.
    message: str             # 核心错误消息
    file_location: str | None  # "src/main.py:42" 或 None
    confidence: str          # "high" | "medium" | "low"
    raw_lines: list[str]     # 原始日志行（最多 5 行上下文）
    occurrence_count: int    # 在此 run 中出现的次数
    step_name: str           # 出现在哪个 step
```

#### F3: Commit 上下文

**获取内容：**
- Commit SHA（短格式）
- Commit message
- Author
- **Diff 摘要**：变更文件列表 + 每个文件的增/删行数（不展示完整 diff——太长）
- 如果变更文件 > 20 个，只展示 top 20 + "…and N more files"

**与错误的关联（0.2 考虑，0.1 仅展示）：**
- 0.1 只并列展示 commit diff 摘要和提取的错误，不做自动关联
- 报告中用视觉分隔暗示"看看这些变更文件和这些错误是否有关系"

#### F4: PR 上下文

**触发条件：** run 的 `event` 字段为 `pull_request` 时启用

**获取内容：**
- PR 标题、编号、作者
- PR 状态：open/merged/closed
- Review 状态：approved / changes_requested / pending
- 最近 5 条 review 评论（截断到每条 200 字符）
- PR body 中的关键信息（截断到 500 字符）

**不获取：**
- PR 的完整 diff（太大，且 commit 上下文已覆盖）
- 所有评论（只取 review 评论，不取普通 issue 评论）

#### F5: 历史模式匹配

**这是 ci-context 的"智能层"——把工具从"日志查看器"提升为"诊断助手"。**

**数据源：** 同一 workflow 的最近 30 次运行（无论成功/失败）

**匹配算法（0.1 简化版）：**

```
1. 获取最近 30 次 runs
2. 对每次失败 run，提取错误指纹（error fingerprint）
3. 计算当前 run 的错误指纹
4. 在历史中搜索相同/相似指纹
5. 如果找到匹配：
   a. 报告："这个错误在过去 N 天出现了 M 次"
   b. 列出匹配的历史 runs（时间、commit message）
   c. 检查历史 runs 的 commit 是否有共同模式
6. 额外：计算 workflow 失败率趋势
```

**错误指纹（Error Fingerprint）计算：**

```python
def compute_fingerprint(error: ExtractedError) -> str:
    """
    错误指纹 = 归一化后的错误类型 + 消息模板

    归一化规则：
    - 移除具体数值（行号、内存地址、时间戳）→ 替换为 <NUM>
    - 移除文件路径中的项目根目录 → 替换为 <ROOT>/
    - 移除变量名（Python NameError 中的名字）→ 替换为 <VAR>
    - 转小写
    - 取前 200 字符

    示例：
    "ModuleNotFoundError: No module named 'numpy'" 
    → "modulenotfounderror: no module named '<var>'"

    "FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/repo/src/main.py'"
    → "filenotfounderror: [errno <num>] no such file or directory: '<root>/src/main.py'"
    """
```

**匹配结果输出：**
- 精确匹配（指纹完全相同）→ `[EXACT] 这个错误在过去 30 次运行中出现 3 次`
- 模糊匹配（指纹相似度 > 0.8，用简单 Levenshtein 距离）→ `[SIMILAR] 类似错误出现过 2 次`
- 无匹配 → `[NEW] 这是首次出现此错误模式`

**workflow 失败率趋势：**
- 最近 30 次 runs 的失败率：`12/30 (40%) failed`
- 最近 10 次 runs 的失败率：`6/10 (60%) failed` → "⚠️ 失败率上升趋势"

#### F6: `--json` 输出

**用途：** 程序化消费（CI 脚本、自定义工具、管道组合）

**JSON Schema（简化）：**

```json
{
  "run": {
    "id": 12345,
    "status": "failure",
    "conclusion": "failure",
    "workflow_name": "CI",
    "head_sha": "abc1234",
    "event": "push",
    "created_at": "2026-07-16T10:30:00Z",
    "url": "https://github.com/owner/repo/actions/runs/12345"
  },
  "errors": [
    {
      "error_type": "Python Traceback",
      "message": "ModuleNotFoundError: No module named 'numpy'",
      "file_location": null,
      "confidence": "high",
      "raw_lines": ["Traceback (most recent call last):", "...", "ModuleNotFoundError: No module named 'numpy'"],
      "occurrence_count": 1,
      "step_name": "Run tests"
    }
  ],
  "commit": {
    "sha": "abc1234def5678",
    "message": "feat: add data processing module",
    "author": "developer",
    "changed_files": [
      {"path": "src/processing.py", "additions": 45, "deletions": 2},
      {"path": "requirements.txt", "additions": 1, "deletions": 0}
    ]
  },
  "pr": null,
  "history": {
    "total_runs_analyzed": 30,
    "failure_rate": "40%",
    "recent_failure_rate": "60%",
    "trend": "increasing",
    "pattern_matches": [
      {
        "fingerprint": "modulenotfounderror: no module named '<var>'",
        "match_type": "exact",
        "occurrence_count": 3,
        "first_seen": "2026-07-10",
        "last_seen": "2026-07-16",
        "related_runs": [12200, 12150, 12345]
      }
    ]
  }
}
```

#### F7: `ci-context gh repo <owner/repo>`

**功能：** 展示仓库最近失败的 runs 摘要

**输出示例：**
```
📊 Recent CI failures for owner/repo (last 7 days)

❌ CI #12345 — 2 hours ago
   Push: feat: add data processing (abc1234)
   Errors: ModuleNotFoundError (high), exit code 1 (low)
   Pattern: [EXACT] seen 3 times in 30 days

❌ CI #12200 — 2 days ago
   Push: fix: update dependencies (def5678)
   Errors: npm ERR! E404 (high)
   Pattern: [NEW] first occurrence

✅ CI #12199 — 2 days ago (passed)
✅ CI #12150 — 3 days ago (passed)
...

Failure rate: 12/30 (40%) | Recent: 6/10 (60%) ⚠️ trending up
```

#### F8: `ci-context gh recent`

**功能：** 在当前 git 仓库目录下运行，自动推断 `owner/repo`，展示最近失败的 runs

**与 F7 的区别：** 不需要手动指定 `owner/repo`，从 `.git/config` 推断

#### F14: 本地失败历史缓存

**0.1 简化版：**
- 位置：`~/.cache/ci-context/history.db`（SQLite）
- 每次运行 `ci-context gh run` 时，将提取的错误指纹和 run 元数据存入缓存
- 下次运行时，先查缓存再调 API，减少 API 调用
- 缓存 TTL：7 天（超过 7 天的记录自动清理）
- `ci-context cache clear`：手动清缓存
- `ci-context cache stats`：查看缓存统计

---

## 6. CLI 命令设计

### 6.1 命令树

```
ci-context
├── gh                          # GitHub Actions 子命令
│   ├── run <run-id>            # 分析单个 run（核心命令）
│   ├── recent                  # 当前仓库最近失败 runs
│   └── repo <owner/repo>       # 指定仓库最近失败 runs
├── cache                       # 缓存管理
│   ├── clear                   # 清除缓存
│   └── stats                   # 缓存统计
├── config                      # 配置管理
│   ├── init                    # 初始化配置
│   ├── show                    # 显示当前配置
│   └── set <key> <value>       # 设置配置项
└── --version                   # 版本信息
    --help                      # 帮助
```

### 6.2 全局选项

| 选项 | 短选项 | 默认值 | 描述 |
|------|--------|--------|------|
| `--repo` | `-r` | 从 git remote 推断 | 仓库标识 `owner/repo` |
| `--json` | `-j` | false | JSON 输出模式 |
| `--no-color` | | false | 禁用彩色输出 |
| `--verbose` | `-v` | false | 详细输出（显示 API 调用等） |
| `--token` | | 从 `gh auth` 或 `GITHUB_TOKEN` env | GitHub API token |

### 6.3 `ci-context gh run` 选项

| 选项 | 默认值 | 描述 |
|------|--------|------|
| `--attempt` | 最新 | 指定 attempt 编号 |
| `--force` | false | 分析非失败 run |
| `--no-history` | false | 跳过历史模式匹配（加速） |
| `--no-pr` | false | 跳过 PR 上下文获取 |
| `--max-history` | 30 | 历史模式匹配的 runs 数量 |
| `--error-lines` | 5 | 每个错误展示的原始日志行数 |

### 6.4 安装方式

```bash
# 方式 1：pip
pip install ci-context

# 方式 2：gh CLI 扩展（0.2 考虑，0.1 不实现）
gh extension install TBNLZLDYD/ci-context

# 方式 3：pipx（推荐，隔离环境）
pipx install ci-context
```

---

## 7. 输出格式设计

### 7.1 终端 Rich 输出（默认）

```
╭─────────────────────────────────────────────────────────────╮
│ 🔍 CI Failure Report — owner/repo                          │
╰─────────────────────────────────────────────────────────────╯

📋 Run Overview
  Run #12345 · CI · failure
  Triggered by push · abc1234 · 2 hours ago
  Duration: 3m 42s · Attempt: 1/1
  URL: https://github.com/owner/repo/actions/runs/12345

❌ Extracted Errors (2 found)

  [high] Python Traceback — ModuleNotFoundError
  ├ Message: No module named 'numpy'
  ├ Step: "Run tests" (step 3/5)
  └ Occurrence: 1 time in this run

  [low] Exit Code
  ├ Code: 1
  └ Step: "Run tests" (step 3/5)

📝 Commit Context
  abc1234 — feat: add data processing module
  Author: developer · 2 hours ago
  Changed files:
    src/processing.py  +45 -2
    requirements.txt   +1  -0  ← ⚠ dependency change

🔀 PR Context
  PR #42: "Add data processing pipeline"
  Status: open · Reviews: 2 approved, 0 changes requested
  Latest review (reviewer1, 1 hour ago):
    "Looks good, but make sure to add numpy to requirements.txt"

📊 History Pattern (30 runs analyzed)

  [EXACT] "modulenotfounderror: no module named '<var>'"
  ├ Occurred 3 times in past 30 days
  ├ First: Jul 10 (run #12200) — "chore: update deps"
  ├ Last:  Jul 16 (run #12345) — "feat: add data processing"
  └ ⚠ All 3 occurrences followed dependency updates

  Failure rate: 12/30 (40%) overall · 6/10 (60%) recent ⚠ trending up

💡 Quick Actions
  View full logs:  gh run view 12345 --log
  View commit:     gh repo view owner/repo --commit abc1234
  View PR:         gh pr view 42
  Re-run failed:   gh run rerun 12345 --failed
```

### 7.2 JSON 输出

见 F6 规格中的 JSON Schema。

### 7.3 输出设计原则

1. **信息密度高但不拥挤**：每个区块有明确标题和视觉分隔
2. **最重要的信息在最前面**：错误提取 → commit 上下文 → PR → 历史
3. **可操作**：底部 Quick Actions 提供后续步骤的 `gh` 命令
4. **渐进式细节**：默认展示摘要，`--verbose` 展示完整原始日志行

---

## 8. 技术架构

### 8.1 技术栈

| 组件栈

| 组件 | 技术选型 | 理由 |
|------|----------|------|
| **CLI 框架** | Typer | 类型安全、自动帮助文档、与 Rich 深度集成 |
| **终端渲染** | Rich | 表格、树、面板、Markdown 渲染、进度条 |
| **GitHub API** | PyGithub | 成熟、类型提示、WorkflowRun/Job API 支持 |
| **HTTP 客户端** | httpx（PyGithub 内部） | 异步支持、连接池 |
| **日志获取** | GitHub REST API（直接） | PyGithub 不支持获取 job logs，需直接调 API |
| **缓存** | SQLite（标准库） | 零依赖、本地文件、足够用 |
| **配置** | TOML（标准库 `tomllib`） | Python 3.11+ 内置、人类可读 |
| **打包** | pyproject.toml + hatchling | 现代 Python 打包标准 |
| **测试** | pytest + pytest-mock + vcrpy | API mock + 录制/回放 |
| **CI** | GitHub Actions | 自身用 GitHub Actions 测试自己（dogfooding） |

### 8.2 Python 版本

**最低支持：Python 3.11**（`tomllib` 内置、`match` 语句成熟、类型提示完善）

### 8.3 项目结构

```
ci-context/
├── pyproject.toml
├── README.md
├── README.zh-CN.md
├── LICENSE (MIT)
├── src/
│   └── ci_context/
│       ├── __init__.py
│       ├── __main__.py          # python -m ci_context 入口
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── main.py          # Typer app + 根命令
│       │   ├── gh.py            # gh 子命令组
│       │   └── cache.py         # cache 子命令组
│       ├── github/
│       │   ├── __init__.py
│       │   ├── client.py        # GitHub API 客户端封装
│       │   ├── runs.py          # WorkflowRun 数据获取
│       │   ├── jobs.py          # Job 数据获取 + 日志获取
│       │   ├── commits.py       # Commit/Diff 数据获取
│       │   ├── prs.py           # PR 数据获取
│       │   └── auth.py          # gh auth 状态检测 + token 获取
│       ├── analysis/
│       │   ├── __init__.py
│       │   ├── extractor.py     # 错误提取引擎
│       │   ├── patterns.py      # 错误模式定义（regex 列表）
│       │   ├── fingerprint.py   # 错误指纹计算
│       │   ├── matcher.py       # 历史模式匹配
│       │   └── normalizer.py    # 日志归一化（去 ANSI 码等）
│       ├── output/
│       │   ├── __init__.py
│       │   ├── rich_renderer.py # Rich 终端渲染
│       │   └── json_renderer.py # JSON 输出
│       ├── cache/
│       │   ├── __init__.py
│       │   └── db.py            # SQLite 缓存管理
│       ├── config/
│       │   ├── __init__.py
│       │   └── settings.py      # 配置管理
│       └── models/
│           ├── __init__.py
│           ├── run.py           # WorkflowRun 数据模型
│           ├── error.py         # ExtractedError 数据模型
│           ├── commit.py        # Commit 数据模型
│           ├── pr.py            # PR 数据模型
│           └── report.py        # 综合报告数据模型
├── tests/
│   ├── conftest.py
│   ├── test_extractor.py
│   ├── test_fingerprint.py
│   ├── test_matcher.py
│   ├── test_cli.py
│   ├── test_client.py
│   └── fixtures/
│       ├── python_traceback.log
│       ├── npm_error.log
│       ├── go_panic.log
│       ├── java_exception.log
│       └── mixed_errors.log
├── .github/
│   └── workflows/
│       ├── ci.yml               # 测试 + lint
│       └── release.yml          # PyPI 发布
└── docs/
    ├── examples.md              # 真实世界示例输出
    └── architecture.md          # 架构说明
```

### 8.4 依赖清单

```toml
[project]
name = "ci-context"
version = "0.1.0b1"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12,<1",
    "rich>=13.0,<14",
    "pygithub>=2.3,<3",
    "httpx>=0.27,<1",        # PyGithub 已依赖，显式声明版本约束
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
    "vcrpy>=6.0",
    "ruff>=0.5",
    "mypy>=1.10",
]
```

**零 AI 依赖。** 不需要 openai、anthropic、dspy 或任何 LLM 库。

---

## 9. 错误提取引擎

### 9.1 架构

```
原始日志 → 日志归一化 → 多模式并行匹配 → 去重与合并 → 置信度标注 → ExtractedError 列表
```

### 9.2 日志归一化

CI 日志通常包含 ANSI 转义码、GitHub Actions 时间戳前缀等噪声。归一化步骤：

1. 移除 ANSI 转义码（`\x1b[...m`）
2. 移除 GitHub Actions 日志前缀（`2026-07-16T10:30:00.1234567Z `）
3. 移除 Docker `##[section]` 标记
4. 移除空行（连续空行压缩为 1 行）
5. 保留原始行号映射（用于 `raw_lines` 输出）

### 9.3 模式定义格式

```python
# src/ci_context/analysis/patterns.py

from dataclasses import dataclass
import re

@dataclass
class ErrorPattern:
    """单个错误模式定义"""
    name: str                    # 人类可读名称："Python Traceback"
    language: str                # "python" | "node" | "go" | "java" | "shell" | "generic"
    confidence: str              # "high" | "medium" | "low"
    # 主匹配 regex：识别错误块的开始
    start_pattern: re.Pattern
    # 消息提取 regex：从匹配块中提取核心消息
    message_pattern: re.Pattern
    # 文件位置提取 regex（可选）
    location_pattern: re.Pattern | None
    # 块结束条件：遇到什么停止提取
    end_condition: re.Pattern | None

# 示例：Python Traceback
PYTHON_TRACEBACK = ErrorPattern(
    name="Python Traceback",
    language="python",
    confidence="high",
    start_pattern=re.compile(r"^Traceback \(most recent call last\):"),
    message_pattern=re.compile(r"^(\w+Error|\w+Exception):\s*(.+)"),
    location_pattern=re.compile(r'^\s+File "(.+)", line (\d+)'),
    end_condition=re.compile(r"^\S"),  # 非缩进行 = traceback 结束
)

# 示例：npm 错误
NPM_ERROR = ErrorPattern(
    name="npm Error",
    language="node",
    confidence="high",
    start_pattern=re.compile(r"^npm ERR!"),
    message_pattern=re.compile(r"^npm ERR!\s+(.+)"),
    location_pattern=None,
    end_condition=re.compile(r"^(?!npm ERR!)"),  # 非 npm ERR! 行 = 结束
)
```

### 9.4 提取算法

```python
def extract_errors(normalized_log: str, patterns: list[ErrorPattern]) -> list[ExtractedError]:
    """
    多模式并行提取算法：
    
    1. 从日志末尾向前扫描（错误通常在尾部）
    2. 对每一行，检查所有 start_pattern
    3. 匹配到 start → 进入"块提取模式"
       a. 继续读取直到 end_condition 或日志结束
       b. 在块内用 message_pattern 提取消息
       c. 在块内用 location_pattern 提取文件位置
    4. 收集所有 ExtractedError
    5. 去重：相同 error_type + message 的错误合并，occurrence_count++
    6. 按置信度排序：high → medium → low
    7. 限制最多返回 10 个错误（避免信息过载）
    """
```

### 9.5 0.1 不做的事

- ❌ 不做跨 step 的错误关联（"step A 的 warning 导致 step B 的 error"）
- ❌ 不做语义理解（"这个 ImportError 是因为 requirements.txt 缺少依赖"——留给用户判断）
- ❌ 不做自定义模式（0.3 插件系统）
- ❌ 不做非 GitHub Actions 格式的日志解析

---

## 10. 历史模式匹配

### 10.1 算法流程

```
当前 run 的错误指纹列表
        │
        ▼
┌─────────────────────┐
│ 获取同 workflow      │
│ 最近 30 次 runs      │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 对每次失败 run：     │
│ 1. 查缓存有无指纹   │──有──→ 使用缓存指纹
│ 2. 无缓存：获取日志  │
│    → 提取错误        │
│    → 计算指纹        │
│    → 存入缓存        │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 指纹匹配：           │
│ - 精确匹配（hash）   │
│ - 模糊匹配（编辑距离）│
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 生成匹配报告：       │
│ - 匹配类型           │
│ - 出现次数           │
│ - 时间范围           │
│ - 关联 commit 模式   │
└─────────────────────┘
```

### 10.2 指纹计算细节

```python
import re
import hashlib

# 归一化替换规则
NORMALIZE_RULES = [
    (re.compile(r'\d+'), '<NUM>'),                    # 数字 → <NUM>
    (re.compile(r'0x[0-9a-fA-F]+'), '<HEX>'),         # 十六进制 → <HEX>
    (re.compile(r'/home/runner/work/[^/]+/'), '<ROOT>/'),  # GitHub Actions 路径
    (re.compile(r'/github/workspace/'), '<ROOT>/'),    # Docker workspace
    (re.compile(r'[a-f0-9]{7,40}'), '<SHA>'),          # Git SHA
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}'), '<TIMESTAMP>'),  # 时间戳
]

def compute_fingerprint(error_type: str, message: str) -> str:
    """
    计算错误指纹：归一化 → 拼接 → SHA256 前 16 字符
    
    为什么用 hash 而非直接比较归一化字符串？
    → hash 更紧凑，SQLite 索引更高效，模糊匹配时计算编辑距离也更快
    """
    normalized = f"{error_type}:{message}".lower()
    for pattern, replacement in NORMALIZE_RULES:
        normalized = pattern.sub(replacement, normalized)
    # 截断到 200 字符避免超长消息导致 hash 碰撞率上升
    normalized = normalized[:200]
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
```

### 10.3 模糊匹配

```python
def fuzzy_match(fp1: str, fp2: str, threshold: float = 0.8) -> bool:
    """
    简化模糊匹配：比较归一化字符串（非 hash）的编辑距离
    
    为什么不直接比较 hash？
    → hash 是单向的，无法计算相似度。需要保留归一化字符串用于模糊匹配。
    
    0.1 简化：只对同 error_type 的指纹做模糊匹配
    → 不同 error_type 的错误几乎不可能"相似"
    """
    # Levenshtein 距离 / max(len) > threshold → 相似
    # 0.1 用简单实现，0.2 考虑 python-Levenshtein 或 rapidfuzz 加速
    ...
```

### 10.4 关联 Commit 模式

当历史匹配找到 ≥ 2 次相同错误时，检查这些 runs 的触发 commit 是否有共同模式：

1. 收集所有匹配 runs 的 commit message
2. 简单关键词提取：找出现 ≥ 2 次的非停用词
3. 检查变更文件交集：这些 runs 是否都改了某些文件

**0.1 输出示例：**
```
⚠ All 3 occurrences followed commits that modified requirements.txt or package.json
```

---

## 11. 数据流与 API 调用

### 11.1 单次 `ci-context gh run 12345` 的 API 调用序列

| 步骤 | API 调用 | 端点 | 预估响应大小 | 缓存策略 |
|------|----------|------|-------------|----------|
| 1 | 获取 run 详情 | `GET /repos/{owner}/{repo}/actions/runs/{run_id}` | ~2 KB | 缓存 5 min |
| 2 | 获取失败 jobs | `GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs` | ~5 KB | 缓存 5 min |
| 3 | 获取 job 日志（每个失败 job） | `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs` | 10-500 KB | 缓存 30 min |
| 4 | 获取 commit diff | `GET /repos/{owner}/{repo}/commits/{sha}` | ~10 KB | 缓存 1 hour |
| 5 | 获取 PR 详情（如果是 PR 触发） | `GET /repos/{owner}/{repo}/pulls/{number}` | ~5 KB | 缓存 5 min |
| 6 | 获取 PR reviews | `GET /repos/{owner}/{repo}/pulls/{number}/reviews` | ~3 KB | 缓存 5 min |
| 7 | 获取 workflow 历史 runs | `GET /repos/{owner}/{repo}/actions/runs?workflow_id={id}&per_page=30` | ~30 KB | 缓存 5 min |
| 8 | 获取历史失败 job 日志（缓存未命中时） | 同步骤 3 | 10-500 KB × N | 缓存 30 min |

### 11.2 API 调用优化

**问题：** 步骤 8 可能触发大量 API 调用（30 次 runs × 每个 1-3 个失败 job = 30-90 次日志获取）

**0.1 优化策略：**

1. **缓存优先**：先查 SQLite 缓存，命中则跳过 API 调用
2. **渐进式获取**：先只获取最近 10 次 runs 的日志（非 30 次），`--max-history` 可调
3. **并行获取**：用 `asyncio` + `httpx` 并行获取多个 job 日志
4. **日志截断**：只获取日志的前 1000 行 + 后 1000 行（错误通常在首尾）
5. **速率限制感知**：检查 `X-RateLimit-Remaining` header，低于阈值时警告并降级

**预估 API 调用数（最坏情况，无缓存）：**
- 首次运行：~15-40 次 API 调用
- 有缓存：~5-8 次 API 调用

**GitHub API 速率限制：**
- 未认证：60 次/小时（不够用）
- `GITHUB_TOKEN`：1,000 次/小时（足够）
- `gh auth` 个人 token：5,000 次/小时（充裕）

**→ 0.1 强制要求 `gh auth` 或 `GITHUB_TOKEN`，未认证时拒绝运行并提示。**

### 11.3 认证流程

```python
def get_github_token() -> str:
    """
    Token 获取优先级：
    1. --token 命令行参数
    2. GITHUB_TOKEN 环境变量
    3. GH_TOKEN 环境变量（gh CLI 使用）
    4. gh auth token 命令输出（调用 gh CLI）
    
    如果都获取不到 → 退出并提示：
    "No GitHub authentication found. Run 'gh auth login' or set GITHUB_TOKEN."
    """
```

---

## 12. 0.1-beta 范围与里程碑

### 12.1 0.1-beta 包含

| 功能 | 状态 |
|------|------|
| `ci-context gh run <run-id>` 核心命令 | ✅ |
| 错误提取引擎（Python/Node/Go/Java/Shell/通用） | ✅ |
| Commit 上下文（diff 摘要） | ✅ |
| PR 上下文（评论 + review 状态） | ✅ |
| 历史模式匹配（指纹 + 模糊匹配 + commit 模式关联） | ✅ |
| Rich 终端输出 | ✅ |
| `--json` 输出 | ✅ |
| `ci-context gh repo <owner/repo>` | ✅ |
| `ci-context gh recent` | ✅ |
| SQLite 缓存 | ✅(简化) |
| `gh auth` / `GITHUB_TOKEN` 认证 | ✅ |
| 双语 README（英文 + 中文） | ✅ |
| 5+ 真实世界示例输出 | ✅ |
| pytest 测试套件 | ✅ |
| GitHub Actions CI（测试 + lint） | ✅ |
| PyPI 发布 | ✅ |

### 12.2 0.1-beta 不包含

| 功能 | 计划版本 | 原因 |
|------|----------|------|
| `--markdown` 文件输出 | 0.2 | 非核心，终端 + JSON 已覆盖主要用例 |
| GitHub Action 集成 | 0.2 | 需要额外的 Action 入口点和 YAML 模板 |
| GitLab CI 支持 | 0.3+ | API 完全不同，需要抽象层 |
| 自定义错误模式插件 | 0.3 | 需要稳定的内部 API 先 |
| 错误-Commit 自动关联 | 0.2 | 0.1 只并列展示，不做自动推理 |
| `gh extension install` 安装 | 0.2 | 需要适配 gh extension 规范 |
| Web Dashboard | 不计划 | 超出 scope，ci-context 是 CLI 工具 |

### 12.3 开发里程碑

```
Week 1 (Jul 16-22): Foundation
├── Day 1-2: 项目脚手架 + pyproject.toml + CLI 骨架
├── Day 3-4: GitHub API 客户端 + 认证 + 获取 run/job 数据
├── Day 5: 日志获取 + 归一化
└── Day 7: PoC 验证 — 能否对真实失败 run 生成基本报告？

Week 2 (Jul 23-29): Intelligence
├── Day 8-9: 错误提取引擎（Python + Node 模式优先）
├── Day 10: 错误提取引擎（Go + Java + Shell + 通用模式）
├── Day 11-12: 错误指纹 + 历史模式匹配
└── Day 14: 里程碑检查 — 错误提取准确率 + 历史匹配是否有用？

Week 3 (Jul 30 - Aug 5): Context & Output
├── Day 15-16: Commit 上下文 + PR 上下文
├── Day 17-18: Rich 终端渲染 + JSON 输出
├── Day 19: `ci-context gh repo` + `ci-context gh recent`
└── Day 21: 里程碑检查 — 完整报告是否比 `gh run view --log-failed` 明显更好？

Week 4 (Aug 6-12): Polish & Release
├── Day 22-23: SQLite 缓存 + API 调用优化
├── Day 24-25: 测试套件完善 + 边界情况处理
├── Day 26: 双语 README + 文档 + 示例输出
├── Day 27: GitHub Actions CI 配置 + PyPI 发布准备
└── Day 28: 🚀 发布 0.1-beta 到 PyPI + GitHub public

Week 5 (Aug 13-19): Promotion
├── Day 29: Show HN 帖子 + Reddit + Twitter
├── Day 30: DEV.to 博客 + V2EX + 掘金
└── Day 31+: 收集反馈 + 规划 0.2
```

### 12.4 关键决策点（Go/No-Go）

| 时间点 | 判断标准 | Go | No-Go → Pivot |
|--------|----------|-----|---------------|
| **Week 1 末** | 能否对真实失败 run 获取日志并提取至少 1 个错误？ | 继续 | 检查 API 限制问题，考虑改用 `gh` CLI wrapper 而非 PyGithub |
| **Week 2 末** | 错误提取在 3+ 真实项目上的准确率 > 70%？ | 继续 | 简化提取引擎，只做 Python + Node，其他语言降级为"exit code only" |
| **Week 3 末** | 完整报告是否比 `gh run view --log-failed` 明显提供更多价值？ | 继续到发布 | 加倍投入历史模式匹配，或考虑加 AI 辅助（0.2） |

---

## 13. 非功能性需求

### 13.1 性能

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| `ci-context gh run` 总耗时（有缓存） | < 5 秒 | `time ci-context gh run 12345` |
| `ci-context gh run` 总耗时（无缓存） | < 15 秒 | 首次运行 |
| `ci-context gh recent` 总耗时 | < 3 秒 | 有缓存 |
| 错误提取耗时（1000 行日志） | < 100 ms | 基准测试 |
| 历史模式匹配耗时（30 runs） | < 3 秒 | 有缓存 |

### 13.2 可靠性

| 场景 | 行为 |
|------|------|
| GitHub API 不可用 | 清晰错误消息 + 重试 1 次（指数退避 2s） |
| API 速率限制 | 提示剩余配额 + 重试时间 + 降级建议 |
| 日志获取失败（已知 gh CLI bug） | 降级：展示 job/step 信息，标注"日志不可获取" |
| Run 不存在 | "Run 12345 not found in owner/repo" |
| 无失败 jobs | "Run completed successfully" + `--force` 提示 |
| 空日志 | "No log output available for this job" |
| 超大日志（> 10MB） | 截断：前 1000 行 + 后 1000 行 + "…skipped N lines…" |
| 网络超时 | 10 秒超时 + 重试 1 次 |

### 13.3 安全

| 要求 | 实现 |
|------|------|
| Token 不落盘 | 不将 token 写入任何文件（除系统 keyring，0.1 不实现） |
| Token 不入日志 | `--verbose` 输出中 token 替换为 `***` |
| 缓存不含敏感数据 | SQLite 缓存只存错误指纹 + run 元数据，不存完整日志 |
| 日志中的 secret 检测 | 0.1 不实现（0.2 考虑：检测并遮蔽 `***SECRET***`） |

### 13.4 可访问性

| 要求 | 实现 |
|------|------|
| `--no-color` | 禁用所有 ANSI 颜色码 |
| `NO_COLOR` 环境变量 | 支持 [no-color.org](https://no-color.org) 标准 |
| JSON 输出 | 完全无颜色，机器可读 |
| `--json` 管道友好 | 检测 stdout 是否为 TTY，非 TTY 时自动禁用进度条 |

### 13.5 兼容性

| 平台 | 支持 | 备注 |
|------|------|------|
| Linux (x86_64) | ✅ | 主要目标 |
| macOS (ARM + x86) | ✅ | Python 跨平台 |
| Windows | ✅ | PowerShell + Git Bash |
| Python 3.11+ | ✅ | 最低版本 |
| Python 3.10 | ❌ | 缺少 `tomllib`，0.2 考虑 `tomli` 回退 |

---

## 14. 风险与缓解

### 14.1 高风险

| # | 风险 | 影响 | 概率 | 缓解策略 |
|---|------|------|------|----------|
| R1 | **输出质量风险**：综合报告可能只是数据堆砌，不比手动跑 3 条 `gh` 命令好 | 产品价值归零 | 中 | **重投历史模式匹配**——这是唯一无法用 3 条 `gh` 命令替代的功能；Week 3 末 Go/No-Go 检查 |
| R2 | **GitHub API 日志获取不稳定**：`gh run view --log` 有已知 bug（大日志失败、空日志） | 核心功能不可用 | 中 | 双路径获取：先尝试 REST API，失败则尝试 `gh run view --log` CLI；降级时标注"日志不可获取" |
| R3 | **GitHub Copilot CLI 功能扩展**：可能加专用 CI 调试命令 | 竞品压力 | 低-中 | 定位为"快速确定性工具"——Copilot 永远需要 AI 推理时间 + token；ci-context 3 秒出结果 |

### 14.2 中风险

| # | 风险 | 影响 | 概率 | 缓解策略 |
|---|------|------|------|----------|
| R4 | **错误提取准确率不足**：regex 启发式对复杂日志可能误提取或漏提取 | 用户信任下降 | 中 | 置信度标注让用户判断；`--verbose` 展示原始行供验证；持续迭代模式库 |
| R5 | **API 速率限制**：大型仓库或频繁使用可能撞限 | 用户体验差 | 低-中 | 激进缓存 + 速率限制感知 + 降级提示；推荐 `GITHUB_TOKEN` |
| R6 | **PyPI 包名被抢注** | 需要换名 | 低 | 尽早注册 `ci-context` 包名（发布前先 `twine upload` 空包占位） |

### 14.3 低风险

| # | 风险 | 影响 | 概率 | 缓解策略 |
|---|------|------|------|----------|
| R7 | **日志格式多样性**：小众框架的日志可能无法提取 | 部分用户无错误提取 | 中 | 0.1 只支持主流语言；通用 `exit code` 兜底；0.3 插件系统 |
| R8 | **推广困难** | Stars 增长慢 | 中 | 天然病毒传播机制（试一次省 10 分钟 → 告诉同事）；多平台推广计划 |

---

## 15. 成功指标

### 15.1 0.1-beta 发布时（Week 4 末）

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| 核心功能完整度 | 100% F1-F5 + F6-F8 + F9 + F14 | 功能清单逐项验证 |
| 错误提取准确率 | > 70%（Python + Node 日志） | 人工标注 50 个真实日志样本 |
| 测试覆盖率 | > 80% | `pytest --cov` |
| 文档完整度 | 双语 README + 5+ 示例输出 | 人工检查 |
| PyPI 可安装 | `pip install ci-context` 成功 | 全新 venv 测试 |
| CI 通过 | GitHub Actions green | 自动 |

### 15.2 发布后 2 周（Week 6 末）

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| GitHub Stars | 30+ | GitHub |
| PyPI 下载量 | 100+ | PyPI stats / pypistats.org |
| 用户反馈 | ≥ 5 条有价值的反馈 | GitHub Issues + Discussions |
| 竞品对比优势确认 | ≥ 3 个用户说"比 gh run view 好" | 反馈收集 |

### 15.3 发布后 1 个月（Week 8 末）

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| GitHub Stars | 80-150 | GitHub |
| 活跃贡献者 | 1-2（除作者外） | GitHub contributors |
| 错误提取准确率 | > 85%（基于用户反馈迭代） | 人工验证 |
| 0.2 规格定义 | 完成 | 文档 |

---

## 16. 未来路线图（0.2+）

### 0.2（发布后 1-2 个月）

- `--markdown` 文件输出
- GitHub Action 集成（CI 内自动运行 + PR 评论）
- 错误-Commit 自动关联（启发式：变更文件与错误文件路径匹配）
- `gh extension install` 安装方式
- 更多错误模式（Rust、C/C++、Ruby、PHP）
- Secret 检测与遮蔽
- `ci-context config init` 交互式配置

### 0.3（发布后 3-4 个月）

- 自定义错误模式插件系统
- GitLab CI 支持
- CircleCI 支持（如果社区有需求）
- Web Dashboard（可选，如果社区驱动）
- 团队共享缓存（Redis/Postgres 后端）

### 长期愿景

ci-context 成为 **CI 失败诊断的标准工具**——就像 `git bisect` 是定位引入 bug 的 commit 的标准工具一样。开发者遇到 CI 失败时的第一反应不是打开浏览器，而是运行 `ci-context`。

---

## 17. 附录

### 17.1 术语表

| 术语 | 定义 |
|------|------|
| **Run** | 一次 GitHub Actions workflow 执行 |
| **Job** | Run 中的一个执行单元（同一 runner 上的一组 steps） |
| **Step** | Job 中的一个原子操作（运行命令或使用 Action） |
| **Error Fingerprint** | 归一化后的错误消息的 hash，用于历史模式匹配 |
| **Error Pattern** | 预定义的 regex 规则，用于从日志中提取结构化错误 |
| **失败上下文** | 理解 CI 失败原因所需的所有相关信息（日志错误 + commit + PR + 历史） |

### 17.2 参考资源

| 资源 | 用途 |
|------|------|
| [GitHub Actions REST API](https://docs.github.com/en/rest/actions) | API 端点参考 |
| [PyGithub WorkflowRun 文档](https://pygithub.readthedocs.io/en/latest/github_objects/WorkflowRun.html) | Python API 参考 |
| [gh CLI manual — gh run view](https://cli.github.com/manual/gh_run_view) | 竞品/互补工具参考 |
| [gha-failure-analysis](https://github.com/marketplace/actions/github-actions-failure-analysis) | AI 驱动竞品参考 |
| [Rich 文档](https://rich.readthedocs.io/) | 终端渲染库 |
| [Typer 文档](https://typer.tiangolo.com/) | CLI 框架 |

### 17.3 决策日志

| 日期 | 决策 | 理由 | 替代方案 |
|------|------|------|----------|
| 2026-07-16 | 使用 PyGithub 而非 `gh` CLI wrapper | 类型安全、更细粒度 API 控制、不依赖 gh CLI 安装 | `subprocess.run(["gh", ...])` — 更简单但无类型安全、依赖外部工具 |
| 2026-07-16 | 0.1 仅支持 GitHub Actions | 专注一个平台做深做透，避免多平台抽象层增加复杂度 | 多平台抽象 — 0.1 时间不够 |
| 2026-07-16 | 错误提取用 regex 而非 AI | 确定性、零成本、快速、可审计 | LLM 提取 — 需要 API key、慢、贵、不确定 |
| 2026-07-16 | Python 3.11 最低版本 | `tomllib` 内置、match 语句、更好的类型提示 | 3.10 + `tomli` — 增加依赖、match 不可用 |
| 2026-07-16 | SQLite 缓存而非纯文件 | 结构化查询、索引、原子写入 | JSON 文件 — 简单但无查询能力、并发写入风险 |

---

*PRD v1.0 — 基于 21 Agent 头脑风暴报告 + 竞品调研撰写 — 2026-07-16*
