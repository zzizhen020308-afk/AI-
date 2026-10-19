"""命令行入口：python -m novel_loop ...（或安装后的 novel ...）。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import LLMConfig, load_llm_config
from .engine import (
    NovelError,
    RoundResult,
    accept,
    format_issues,
    intake,
    plan,
    run_loop,
    status,
    word_count,
    write_round,
)
from .llm import LLMError, build_provider
from .prompts import PROMPT_NAMES, PromptError, PromptStore
from .store import (
    BACKGROUND_TEMPLATE,
    PREMISE_TEMPLATE,
    STYLE_TEMPLATE,
    Project,
    StoreError,
)

BAR = "─" * 64
STATUS_LABEL = {
    "planned": "待写",
    "drafting": "起草中",
    "in_review": "待你审",
    "accepted": "已定稿",
}


# ---------------------------------------------------------------- 展示


def _render_result(result: RoundResult) -> str:
    lines = [
        "",
        BAR,
        "第 %d 章 · 第 %d 稿 · %d 字" % (result.chapter, result.revision, word_count(result.draft)),
        BAR,
        result.draft,
        "",
        BAR,
    ]
    if result.auto_fixed:
        lines.append("（自评发现硬伤，已自动重写一版）")
    if result.critique.overall:
        lines.append("编辑自评：%s" % result.critique.overall)
    if result.critique.issues:
        lines.append("待处理：")
        lines.extend("  " + line for line in format_issues(result.critique.issues).splitlines())
    if result.critique.questions:
        lines.append("需要你拍板：")
        lines.extend("  %d. %s" % (i, q) for i, q in enumerate(result.critique.questions, start=1))
    return "\n".join(lines)


def _print_status(project: Project) -> None:
    meta = project.meta()
    rows = status(project)
    print("《%s》" % meta.get("title", ""))
    print("视角：%s · 时态：%s · 语言：%s" % (meta.get("pov", ""), meta.get("tense", ""), meta.get("language", "")))
    if not rows:
        print("\n还没有章节。运行 novel plan 生成大纲。")
        return
    print("")
    accepted = sum(1 for row in rows if row["status"] == "accepted")
    print("%-4s %-18s %-6s %-5s %-6s %s" % ("章", "标题", "状态", "轮次", "稿次", "字数"))
    for row in rows:
        print(
            "%-4d %-18s %-6s %-5d %-6d %d"
            % (
                row["number"],
                row["title"][:16],
                STATUS_LABEL.get(row["status"], row["status"]),
                row["rounds"],
                row["revision"],
                row["words"],
            )
        )
    total = sum(row["words"] for row in rows if row["status"] == "accepted")
    print("\n进度：%d/%d 章定稿，累计 %d 字。" % (accepted, len(rows), total))


def _brief_state(text: str, template: str = "") -> str:
    """判断设定文件是空的、还是仍是初始模板、还是已经写了东西。"""
    if not text.strip():
        return "空"
    if template and text.strip() == template.strip():
        return "仍是模板"
    meaningful = []
    template_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(">"):
            continue
        if re.match(r"^-\s*[^:：]+[:：]\s*$", stripped) or stripped.startswith("（"):
            template_lines += 1
            continue
        meaningful.append(stripped)
    if not meaningful:
        return "仍是模板" if template_lines else "空"
    return "已填写"


def _print_brief_check(project: Project) -> None:
    checks = [
        ("背景", "background"),
        ("前提", "premise"),
        ("风格", "style"),
    ]
    print("\n设定检查：")
    templates = {
        "background": BACKGROUND_TEMPLATE,
        "premise": PREMISE_TEMPLATE,
        "style": STYLE_TEMPLATE,
    }
    for label, name in checks:
        state = _brief_state(project.brief_text(name), templates[name])
        print("  %s：%s（brief/%s.md）" % (label, state, name))
    characters = project.characters()
    print("  人物卡：%d 个%s" % (len(characters), "：" + "、".join(characters) if characters else "（还没有）"))


# ---------------------------------------------------------------- 命令


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if (root / "project.json").is_file():
        print("项目已存在：%s（不会覆盖）" % root.resolve())
        return 0
    project = Project.create(root, title=args.title)
    print("已创建小说项目 → %s" % root.resolve())
    print("\n下一步：")
    print("  1. 编辑 %s 下的 background / premise / style / characters" % (root / "brief"))
    print("  2. novel intake      # 让 AI 先追问你的设定漏洞")
    print("  3. novel plan        # 生成分章大纲")
    print("  4. novel loop 1      # 进入第 1 章的往返循环")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    _print_status(project)
    _print_brief_check(project)
    return 0


def cmd_intake(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    provider = _provider()
    prompts = PromptStore.locate(project.root)
    data = intake(project, provider, prompts)
    print("\n【设定里还缺的东西】")
    for item in data.get("gaps", []) or ["（无）"]:
        print("  - %s" % item)
    print("\n【AI 想先问你】")
    for index, item in enumerate(data.get("questions", []) or ["（无）"], start=1):
        print("  %d. %s" % (index, item))
    if data.get("risks"):
        print("\n【它担心的风险】")
        for item in data["risks"]:
            print("  - %s" % item)
    print("\n建议：把回答补进 brief/premise.md 或对应人物卡，然后再 novel plan。")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    provider = _provider()
    prompts = PromptStore.locate(project.root)
    chapters = plan(project, provider, prompts, args.chapters)
    print("\n已生成 %d 章大纲：" % len(chapters))
    for chapter in chapters:
        print("\n第 %d 章《%s》" % (chapter.number, chapter.title))
        for beat in chapter.beats:
            print("  - %s" % beat)
    print("\n可以手动改 %s 里 chapters 的 beats，然后 novel loop 1。" % (project.root / "project.json"))
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    provider = _provider()
    prompts = PromptStore.locate(project.root)
    result = write_round(project, provider, prompts, args.number)
    print(_render_result(result))
    print("\n用 novel loop %d 进入往返修改，或 novel accept %d 直接定稿。" % (args.number, args.number))
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    provider = _provider()
    prompts = PromptStore.locate(project.root)

    def ask(prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            return "q"

    final = run_loop(
        project,
        provider,
        prompts,
        args.number,
        ask=ask,
        say=print,
        render=_render_result,
        max_rounds=args.max_rounds,
    )
    print("\n当前状态：%s" % STATUS_LABEL.get(final, final))
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    provider = _provider()
    prompts = PromptStore.locate(project.root)
    data = accept(project, provider, prompts, args.number)
    print("\n第 %d 章已定稿。" % args.number)
    if data.get("summary"):
        print("摘要：%s" % data["summary"])
    facts = data.get("canon", [])
    if facts:
        print("新增 canon 事实：")
        for fact in facts:
            print("  - %s" % fact)
    if data.get("open_threads"):
        print("留下的线索：")
        for thread in data["open_threads"]:
            print("  - %s" % thread)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    chapter = project.chapter(args.number)
    draft = project.draft(args.number)
    if not draft:
        print("这一章还没有草稿，先运行 novel write %d。" % args.number)
        return 0
    print(BAR)
    print("第 %d 章《%s》· 第 %d 稿 · %d 字 · %s"
          % (chapter.number, chapter.title, chapter.current_revision, word_count(draft),
             STATUS_LABEL.get(chapter.status, chapter.status)))
    print(BAR)
    print(draft)
    critique = project.critique(args.number, chapter.current_revision)
    if critique:
        print("\n" + BAR)
        if critique.get("overall"):
            print("编辑自评：%s" % critique["overall"])
        for line in format_issues(critique.get("issues", [])).splitlines():
            print("  " + line)
        for index, question in enumerate(critique.get("questions", []), start=1):
            print("  ? %d. %s" % (index, question))
    notes = [item for item in project.feedbacks(args.number) if item.get("text")]
    if notes:
        print("\n历史反馈：")
        for index, item in enumerate(notes, start=1):
            print("  %d. %s" % (index, item["text"][:120]))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    project = Project.load(Path(args.root))
    revisions = project.revisions(args.number)
    if not revisions:
        print("第 %d 章还没有任何稿。" % args.number)
        return 0
    if args.show:
        print(project.read_revision(args.number, args.show))
        return 0
    print("第 %d 章共 %d 稿：" % (args.number, len(revisions)))
    for revision in revisions:
        text = project.read_revision(args.number, revision)
        mark = " ←当前" if revision == project.chapter(args.number).current_revision else ""
        print("  %04d  %5d 字%s" % (revision, word_count(text), mark))
    print("\nnovel history %d --show 3   查看某一稿" % args.number)
    print("novel history %d --restore 3 回滚到某一稿" % args.number)
    if args.restore:
        project.restore_revision(args.number, args.restore)
        print("已回滚到第 %d 稿。" % args.restore)
    return 0


def cmd_prompts(args: argparse.Namespace) -> int:
    store = PromptStore.locate(Path(args.root))
    print("提示词目录：%s" % store.directory)
    for name in PROMPT_NAMES:
        path = store.path(name)
        print("  %-10s %s" % (name, "✓" if path.is_file() else "缺失"))
    if args.show:
        system, user = store.load(args.show)
        print("\n=== system ===\n%s\n\n=== user ===\n%s" % (system, user))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config()
    print("版本：novel-loop %s" % __version__)
    print("Python：%s" % sys.version.split()[0])
    print("模型：%s" % config.describe())
    if config.is_mock:
        print("提示：未检测到 NOVEL_API_KEY，当前为离线 mock 模式（只验证流程，不产生真实内容）。")
        print("      复制 .env.example 为 .env 并填入密钥即可接入真实模型。")
    try:
        store = PromptStore.locate(Path(args.root))
        print("提示词目录：%s" % store.directory)
    except PromptError as exc:
        print("提示词目录：%s" % exc)
    root = Path(args.root)
    if (root / "project.json").is_file():
        project = Project.load(root)
        print("项目：%s（%d 章）" % (root.resolve(), len(project.chapters())))
    else:
        print("项目：尚未初始化（运行 novel init）")
    return 0


# ---------------------------------------------------------------- 装配


def _config() -> LLMConfig:
    return load_llm_config(env_file=Path.cwd() / ".env")


def _provider():
    return build_provider(_config())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novel",
        description="与 AI 往返协作写长篇小说：起草 → 自评 → 反馈 → 修订 → 定稿",
    )
    parser.add_argument("--root", default="novel", help="小说项目目录（默认 ./novel）")
    parser.add_argument("--version", action="version", version="novel-loop " + __version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="新建小说项目")
    p.add_argument("--title", default="未命名小说")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="查看章节进度与设定完备度")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("intake", help="让 AI 先追问设定漏洞")
    p.set_defaults(func=cmd_intake)

    p = sub.add_parser("plan", help="生成分章大纲")
    p.add_argument("--chapters", type=int, default=8)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("write", help="跑一轮（起草或修订），不进入交互")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("loop", help="进入往返循环，直到接受或退出")
    p.add_argument("number", type=int)
    p.add_argument("--max-rounds", type=int, default=None)
    p.set_defaults(func=cmd_loop)

    p = sub.add_parser("accept", help="定稿：写摘要、抽出 canon 事实")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("review", help="看当前稿、自评与历史反馈")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("history", help="查看或回滚历史稿")
    p.add_argument("number", type=int)
    p.add_argument("--show", type=int, default=None)
    p.add_argument("--restore", type=int, default=None)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("prompts", help="查看提示词文件")
    p.add_argument("--show", choices=PROMPT_NAMES, default=None)
    p.set_defaults(func=cmd_prompts)

    p = sub.add_parser("doctor", help="自检环境与配置")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except (StoreError, NovelError, LLMError, PromptError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
