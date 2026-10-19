# novel-loop

**和 AI 一起写长篇小说，而不是让 AI 替你写一篇。**

你提供背景、情节概要、人物；引擎把它变成可执行的分章大纲，然后**一章一章地和你来回磨**：
AI 起草 → AI 自评 → 你提意见 → AI 改 → 你满意了才定稿。
定稿时才把这一章"确定下来的事实"写进长期记忆，供后面的章节使用。

> 仓库里的 `novel_loop/` 是引擎，`novel/` 是你的小说工作区（纯文本，可以 `git diff`，可以回滚到任意一稿）。

---

## 为什么不"一次性生成整本"

| 一次性生成 | 这个项目 |
|---|---|
| 模型必须在一次上下文里记住整本书 | 每章只带"必要的上下文包"：设定 + canon + 前情摘要 + 本章 beat + 你的反馈 |
| 改一处，全篇重写，越改越偏 | 每次修改是一个新 `revision`，旧稿留着，随时 `--restore` |
| 你只能接受或重来 | 你可以像编辑一样逐条提意见，AI 逐条落实 |
| 前后矛盾只能靠模型自觉 | 每章定稿后抽取 canon 事实，后续章节的提示词里明确"不可违背" |
| 文风漂移无人管 | 独立的编辑自评会按风格指令挑错，硬伤自动重写一版再给你看 |

## 快速开始

```bash
cd ai-novel
cp .env.example .env     # 填 NOVEL_API_KEY；不填则进入离线 mock 模式（流程照跑）

# 仓库里已带一个空白项目 novel/，直接写你的设定：
#   novel/brief/background.md   世界、时代、地点、特殊规则
#   novel/brief/premise.md      一句话故事、主线、结局方向、禁忌
#   novel/brief/style.md        视角、句子偏好、参考作家、禁忌写法
#   novel/brief/characters/     一个人物一个文件（从 _模板.md 复制）

novel intake             # 让 AI 先追问你的设定漏洞
novel plan               # 生成分章大纲（之后可手改 beats）
novel loop 1             # 进入第 1 章的往返循环
```

想另起一个新项目、不动仓库里这个：

```bash
novel init --title "另一本书" --root ~/novels/another
```

没装包也能直接跑（Python 3.9+，零第三方依赖）：

```bash
python3 -m novel_loop init --title "雨夜收据"
```

## 写作循环长什么样

```
        你：背景 / 情节概要 / 人物 / 风格
                     │
                     ▼
        ┌─────── 每一章都是一个循环 ───────┐
        │                                  │
        │  ① 组装上下文包                  │
        │     设定 + canon + 前情摘要      │
        │     + 本章 beat + 历次反馈       │
        │              ▼                   │
        │  ② 起草（第 1 稿）/ 修订（后续） │
        │              ▼                   │
        │  ③ AI 自评：continuity /         │
        │     character / pacing / style / │
        │     logic；有硬伤 → 自动重写一次 │
        │              ▼                   │
        │  ④ 呈给你：正文 + 自评 + 待定问题│
        │              ▼                   │
        │  ⑤ 你的反馈（或 a=接受 / q=退出）│
        │              └──── 继续 → 回到 ② │
        └──────────────────┬───────────────┘
                           ▼ 接受
              ⑥ 归档：本章摘要 + 新增 canon 事实
```

真实跑起来是这样（下面是 mock 模式的输出，用于演示交互节奏）：

```
────────────────────────────────────────────────────────────────
第 1 章 · 第 1 稿 · 205 字
────────────────────────────────────────────────────────────────
（mock 正文·第 1 章《收据》：离线占位文本……）

雨在凌晨三点停了，空气里全是铁锈味。他站在便利店门口，手里那张收据已经被汗浸软。……

────────────────────────────────────────────────────────────────
编辑自评：（mock）整体可读，中段节奏偏平。
待处理：
  - [pacing/minor] （mock）第二段缺少转折，读者容易掉出去。 → 在第二段末尾加一个意外发现。
需要你拍板：
  1. （mock）主角此刻最怕失去什么？
  2. （mock）这一章结尾要不要留一个钩子？

反馈（回车=让 AI 自行打磨，a=接受，q=退出，或直接写修改意见）> 结尾再狠一点，让他把收据烧掉
```

下一轮 AI 拿到的就是这一句反馈 + 上一稿，只改该改的地方。你说 `a`，它就归档摘要与 canon，进入第 2 章。

## 你的工作区

```
novel/
├── project.json              章节列表与状态（可手改 beats、目标字数）
├── brief/
│   ├── background.md         世界 / 时代 / 地点 / 特殊规则
│   ├── premise.md            一句话故事、主线、结局方向、禁忌
│   ├── style.md              视角、句子偏好、参考作家、禁忌写法
│   └── characters/
│       ├── _模板.md          人物卡模板（下划线开头的文件会被忽略）
│       └── 陈默.md           一个文件一个人物
├── chapters/ch01/
│   ├── draft.md              当前稿
│   ├── revisions/0001.md     每一稿都在，永不覆盖
│   ├── critique/0001.json    该稿的自评记录
│   └── feedback.jsonl        你给过的每条反馈
├── memory/
│   ├── canon.md              已确立事实，优先级最高
│   └── summaries/ch01.md     定稿后自动生成的本章摘要
└── .novel/log.jsonl          操作日志（已 gitignore）
```

## 命令

| 命令 | 作用 |
|---|---|
| `novel init --title "…"` | 新建项目骨架（已存在不会覆盖） |
| `novel status` | 章节进度 + 设定完备度检查 |
| `novel intake` | 开工前请 AI 指出设定空洞、追问、报风险 |
| `novel plan --chapters 8` | 生成分章大纲（只出 beat，不写正文） |
| `novel loop 3` | **核心**：第 3 章往返循环，直到接受或退出 |
| `novel write 3` | 只跑一轮（起草或修订），不进入交互，适合脚本化 |
| `novel review 3` | 看当前稿 + 自评 + 历史反馈 |
| `novel accept 3` | 直接定稿：写摘要、抽 canon |
| `novel history 3` | 列出所有稿；`--show 2` 看某稿；`--restore 2` 回滚 |
| `novel prompts --show draft` | 查看提示词原文 |
| `novel doctor` | 自检：模型配置、提示词目录、项目状态 |

## 接入模型

任何 OpenAI 兼容接口都能用，改 `.env` 即可：

```bash
# DeepSeek
NOVEL_API_BASE=https://api.deepseek.com/v1
NOVEL_MODEL=deepseek-chat

# OpenAI
NOVEL_API_BASE=https://api.openai.com/v1
NOVEL_MODEL=gpt-4o-mini

# 本地 Ollama
NOVEL_API_BASE=http://localhost:11434/v1
NOVEL_MODEL=qwen2.5:14b
```

**不填 `NOVEL_API_KEY` 时自动使用 mock provider**：流程、文件、状态机全部真实运行，只有正文是占位文本。这让整个循环可以在没有密钥、没有网络、跑测试的情况下被完整验证。

写长篇建议：正文用能力强的模型（`temperature` 0.7~0.9），自评/抽 canon 可以用便宜模型——目前共用一个配置，见路线图。

## 把设定写好，比把提示词写好更重要

`intake` 会替你挑刺，但下面几件事只有你能定：

1. **一句话故事**要有代价：谁、在什么处境、要做什么、失败会失去什么。
2. **人物卡写"会做什么/绝不会做什么"**，比写外貌有用得多——AI 靠这个判断人物崩没崩。
3. **风格写禁忌**：不要总结式抒情、不要心理直述、不要文绉绉的对话。负面清单比正面形容词有效。
4. **beat 要具体到动作**："主角发现收据上的字不是自己的" 比 "悬念升级" 有用。

大纲生成后直接改 `novel/project.json` 里的 `beats` 就行，引擎每轮都重新读。

## 记忆与回滚

- **canon** 是硬约束。每章 `accept` 时由 AI 抽取"以后写错就出 bug"的事实（谁死了、东西交给谁了、秘密被谁知道了），也可以手动加。
- **摘要** 让第 20 章不需要读前 19 章全文。`summaries` 只喂"本章之前、已定稿"的章节。
- **revisions** 是完整快照。改坏了 `novel history 3 --restore 1` 一句话回到第 1 稿，而且当前稿会同步。

## 改提示词

`prompts/*.md` 就是全部提示词，格式：

```
::: system
……
::: user
……{{变量}}……
```

六个文件分别对应六个阶段：`intake`、`outline`、`draft`、`revise`、`critique`、`accept`。
想让自评更严格、让大纲更阴暗、让对话更短——改文件即可，不用动 Python。想给某本书单独定制，把 `prompts/` 复制到 `novel/prompts/`，会优先使用项目内的版本。

## 开发

```bash
python3 -m unittest discover -s tests -v   # 32 个测试，零依赖
python3 -m novel_loop doctor
```

设计取舍、状态机、扩展点见 [docs/DESIGN.md](docs/DESIGN.md)。

## 已知限制 / 路线图

- [ ] 自评和正文共用一个模型；接第二模型（便宜模型做编辑）能显著降本
- [ ] 上下文是全量拼接，超长篇需要按相关性检索 canon（目前靠摘要压缩）
- [ ] 没有 Web UI；阅读体验目前是终端 + Markdown
- [ ] 多视角/多线索并行的章节还没有专门的支持
- [ ] 没有做"整本书一致性复查"（跨章矛盾检测）

## License

MIT
