"""循环内核。

每一轮 write_round 做四件事：
    1. 组装上下文包（设定 + canon + 前情摘要 + 本章 beat + 历次反馈）
    2. 起草（第 1 稿）或修订（后续稿）
    3. 自评：按固定检查表挑错，critical 级硬伤触发一次自动重写
    4. 落盘为一个新 revision，交给作者看

作者接受（accept）时才写入记忆：本章摘要 + 新的 canon 事实。
这样后续章节永远有"这本书已经确定的事"，不靠模型一次记住整本书。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .llm import Provider
from .prompts import PromptStore
from .store import Chapter, Project

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}


class NovelError(RuntimeError):
    """流程或数据错误。"""


@dataclass
class Critique:
    overall: str = ""
    issues: List[Dict[str, Any]] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(str(issue.get("severity", "")).lower() == "critical" for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {"overall": self.overall, "issues": self.issues, "questions": self.questions}


@dataclass
class RoundResult:
    chapter: int
    revision: int
    draft: str
    critique: Critique
    auto_fixed: bool = False


# ---------------------------------------------------------------- 工具


def extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON：兼容 ```json 代码块、前后废话、裸对象。"""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except ValueError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except ValueError:
            continue
    raise NovelError("模型没有返回可解析的 JSON：%s" % text[:200])


def word_count(text: str) -> int:
    """中文按字符计，英文按词计——这里统一用「非空白字符数」，够用且稳定。"""
    return len(re.sub(r"\s+", "", text))


def format_feedback(items: List[Dict[str, Any]]) -> str:
    notes = [item for item in items if str(item.get("kind", "note")) != "system"]
    if not notes:
        return "（暂无）"
    lines = []
    for index, item in enumerate(notes, start=1):
        text = str(item.get("text", "")).strip()
        if text:
            lines.append("第 %d 轮：%s" % (index, text))
    return "\n".join(lines) if lines else "（暂无）"


def format_issues(issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return ""
    ordered = sorted(issues, key=lambda item: SEVERITY_ORDER.get(str(item.get("severity", "")), 9))
    lines = []
    for issue in ordered:
        lines.append(
            "- [%s/%s] %s → %s"
            % (
                issue.get("type", "?"),
                issue.get("severity", "?"),
                issue.get("detail", ""),
                issue.get("fix", ""),
            )
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- 上下文包


def build_context(project: Project, chapter: Chapter) -> Dict[str, str]:
    meta = project.meta()
    accepted_before = [
        item.number
        for item in project.chapters()
        if item.number < chapter.number and item.status == "accepted"
    ]
    characters = project.characters()
    if characters:
        blocks = ["### %s\n%s" % (name, text) for name, text in characters.items()]
        character_text = "\n\n".join(blocks)
    else:
        character_text = "（作者还没有提供人物卡，请在 brief/characters/ 下补充）"

    beats = chapter.beats or []
    outline = "\n".join("- " + beat for beat in beats) if beats else "（未设置 beat，请自然推进）"

    return {
        "title": str(meta.get("title", "")),
        "language": str(meta.get("language", "zh")),
        "pov": str(meta.get("pov", "")),
        "tense": str(meta.get("tense", "")),
        "background": project.brief_text("background") or "（未提供）",
        "premise": project.brief_text("premise") or "（未提供）",
        "style": project.brief_text("style") or "（未提供，请写得克制、具体、少形容词）",
        "characters": character_text,
        "canon": project.canon() or "（暂无，这是最早的内容）",
        "summaries": project.summaries(accepted_before) or "（本章是最早被写作的内容，没有前情）",
        "outline": outline,
        "chapter_number": str(chapter.number),
        "chapter_title": chapter.title,
        "target_words": str(chapter.target_words),
        "feedback": format_feedback(project.feedbacks(chapter.number)),
        "draft": project.draft(chapter.number),
    }


# ---------------------------------------------------------------- 各阶段


def intake(project: Project, provider: Provider, prompts: PromptStore) -> Dict[str, Any]:
    """开工前先让 AI 追问：设定里哪里不够、有什么风险。"""
    context = {
        "title": str(project.meta().get("title", "")),
        "background": project.brief_text("background") or "（未提供）",
        "premise": project.brief_text("premise") or "（未提供）",
        "style": project.brief_text("style") or "（未提供）",
        "characters": "\n\n".join(project.characters().values()) or "（未提供）",
    }
    system, user = prompts.render("intake", context)
    data = extract_json(provider.complete(system, user, task="intake"))
    if not isinstance(data, dict):
        raise NovelError("intake 阶段期望返回 JSON 对象")
    project.log("intake", questions=len(data.get("questions", [])))
    return data


def plan(
    project: Project, provider: Provider, prompts: PromptStore, chapter_count: int = 8
) -> List[Chapter]:
    """根据设定生成分章大纲（只生成 beat，不写正文）。"""
    context = {
        "title": str(project.meta().get("title", "")),
        "background": project.brief_text("background") or "（未提供）",
        "premise": project.brief_text("premise") or "（未提供）",
        "style": project.brief_text("style") or "（未提供）",
        "characters": "\n\n".join(project.characters().values()) or "（未提供）",
        "chapter_count": str(chapter_count),
    }
    system, user = prompts.render("outline", context)
    data = extract_json(provider.complete(system, user, task="outline"))
    items = data.get("chapters") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise NovelError("大纲为空或格式不对，期望 {\"chapters\": [...]}")

    target_words = int(project.meta().get("default_target_words", 2500))
    chapters: List[Chapter] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        beats = item.get("beats") or item.get("plot") or []
        if isinstance(beats, str):
            beats = [beats]
        chapters.append(
            Chapter(
                number=index,
                title=str(item.get("title") or ("第 %d 章" % index)),
                beats=[str(beat) for beat in beats],
                target_words=target_words,
            )
        )
    if not chapters:
        raise NovelError("大纲里没有任何有效的章节")
    project.set_chapters(chapters)
    project.log("plan", chapters=len(chapters))
    return chapters


def _critique(
    project: Project,
    provider: Provider,
    prompts: PromptStore,
    chapter: Chapter,
    draft: str,
    context: Dict[str, str],
) -> Critique:
    local = dict(context)
    local["draft"] = draft
    system, user = prompts.render("critique", local)
    data = extract_json(provider.complete(system, user, task="critique"))
    if not isinstance(data, dict):
        return Critique()
    issues = [item for item in data.get("issues", []) if isinstance(item, dict)]
    questions = [str(item) for item in data.get("questions", [])][:5]
    return Critique(str(data.get("overall", "")), issues, questions)


def write_round(
    project: Project,
    provider: Provider,
    prompts: PromptStore,
    number: int,
    extra_note: str = "",
) -> RoundResult:
    """跑一轮：起草或修订 → 自评 → （必要时）自动修硬伤 → 落盘成新稿。"""
    chapter = project.chapter(number)
    context = build_context(project, chapter)
    if extra_note.strip():
        context["feedback"] = (context["feedback"] + "\n" + extra_note.strip()).strip()

    template = "revise" if chapter.current_revision > 0 else "draft"
    system, user = prompts.render(template, context)
    text = provider.complete(system, user, task=template).strip()
    if not text:
        raise NovelError("模型返回了空文本，请检查模型与接口配置")

    revision = project.write_revision(chapter, text)
    draft = project.draft(number)
    critique = _critique(project, provider, prompts, chapter, draft, context)
    auto_fixed = False

    if critique.blocking and bool(project.meta().get("auto_fix", True)):
        fix_context = dict(context)
        fix_context["draft"] = draft
        fix_context["feedback"] = (
            "上一稿自评发现硬伤，请修正后重写整章（保留没问题的地方）：\n"
            + format_issues(critique.issues)
        )
        system, user = prompts.render("revise", fix_context)
        fixed = provider.complete(system, user, task="revise").strip()
        if fixed:
            revision = project.write_revision(chapter, fixed)
            draft = project.draft(number)
            critique = _critique(project, provider, prompts, chapter, draft, context)
            auto_fixed = True

    chapter.status = "in_review"
    chapter.rounds += 1
    project.save_chapter(chapter)
    project.save_critique(number, revision, critique.to_dict())
    project.log(
        "write_round", chapter=number, revision=revision, auto_fixed=auto_fixed, words=word_count(draft)
    )
    return RoundResult(number, revision, draft, critique, auto_fixed)


def accept(
    project: Project, provider: Provider, prompts: PromptStore, number: int
) -> Dict[str, Any]:
    """定稿：生成摘要、抽取 canon 事实、标记 accepted。"""
    chapter = project.chapter(number)
    draft = project.draft(number)
    if not draft:
        raise NovelError("第 %d 章还没有草稿，先运行 novel write %d" % (number, number))

    context = build_context(project, chapter)
    context["draft"] = draft
    system, user = prompts.render("accept", context)
    data = extract_json(provider.complete(system, user, task="accept"))
    if not isinstance(data, dict):
        raise NovelError("accept 阶段期望返回 JSON 对象")

    summary = str(data.get("summary", "")).strip()
    if summary:
        project.write_summary(number, summary)
    facts = [str(item).strip() for item in data.get("canon", []) if str(item).strip()]
    project.append_canon(facts)

    chapter.status = "accepted"
    project.save_chapter(chapter)
    project.log("accept", chapter=number, canon_added=len(facts))
    return data


def status(project: Project) -> List[Dict[str, Any]]:
    rows = []
    for chapter in project.chapters():
        rows.append(
            {
                "number": chapter.number,
                "title": chapter.title,
                "status": chapter.status,
                "rounds": chapter.rounds,
                "revision": chapter.current_revision,
                "words": word_count(project.draft(chapter.number)),
            }
        )
    return rows


# ---------------------------------------------------------------- 交互循环


def run_loop(
    project: Project,
    provider: Provider,
    prompts: PromptStore,
    number: int,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    render: Callable[[RoundResult], str],
    max_rounds: Optional[int] = None,
) -> str:
    """往返循环：写一轮 → 给作者看 → 收反馈 → 再写一轮，直到 accept 或退出。

    作者可以直接输入修改意见（会进入下一轮的上下文），也可以用指令：
        a / accept  接受本章，写入记忆
        q / quit    保存并退出，下次接着来
    """
    chapter = project.chapter(number)
    limit = int(max_rounds or project.meta().get("max_rounds", 6))
    while chapter.status != "accepted" and chapter.rounds < limit:
        result = write_round(project, provider, prompts, number)
        say(render(result))
        reply = ask(
            "反馈（回车=让 AI 自行打磨，a=接受，q=退出，或直接写修改意见）> "
        ).strip()
        lowered = reply.lower()
        if lowered in ("a", "accept", "y", "yes"):
            info = accept(project, provider, prompts, number)
            say(
                "已接受第 %d 章：新增 %d 条 canon 事实，摘要已归档到 memory/。"
                % (number, len(info.get("canon", [])))
            )
            return "accepted"
        if lowered in ("q", "quit", "exit"):
            return chapter.status
        project.add_feedback(number, reply or "（没有具体意见，请你自己再打磨一轮）")
        chapter = project.chapter(number)
    if chapter.status != "accepted":
        say("已达到本轮上限（%d 轮），先停在这里。运行 novel loop %d 可以继续。" % (limit, number))
    return chapter.status
