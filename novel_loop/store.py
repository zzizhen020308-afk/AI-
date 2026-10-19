"""项目落盘层：一个小说项目 = 一个可 diff、可回滚的普通目录。

    project.json          结构化元数据：标题、视角、章节列表与状态
    brief/                作者提供的设定（背景 / 前提 / 风格 / 人物卡）
    chapters/chNN/        每章一目录：draft.md、revisions/、feedback.jsonl、critique/
    memory/canon.md       已确立事实，优先级最高，任何章节不得违背
    memory/summaries/     每章被接受后的摘要，供后续章节回忆
    .novel/log.jsonl      操作日志
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

META_NAME = "project.json"
CHAPTER_STATUSES = ("planned", "drafting", "in_review", "accepted")

CANON_HEADER = """# 已确立的事实（canon）

> 每章被接受后由引擎自动追加；你也可以手动改。这里的内容优先级最高，
> 后续任何章节都不得违背。一条事实一行，尽量具体。
"""


class StoreError(RuntimeError):
    """项目结构缺失或数据损坏。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Chapter:
    number: int
    title: str
    beats: List[str] = field(default_factory=list)
    status: str = "planned"
    rounds: int = 0
    current_revision: int = 0
    target_words: int = 2500

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "beats": list(self.beats),
            "status": self.status,
            "rounds": self.rounds,
            "current_revision": self.current_revision,
            "target_words": self.target_words,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Chapter":
        return cls(
            number=int(raw.get("number", 0)),
            title=str(raw.get("title", "")),
            beats=[str(item) for item in raw.get("beats", [])],
            status=str(raw.get("status", "planned")),
            rounds=int(raw.get("rounds", 0)),
            current_revision=int(raw.get("current_revision", 0)),
            target_words=int(raw.get("target_words", 2500)),
        )


class Project:
    def __init__(self, root: Path, meta: Optional[Dict[str, Any]] = None) -> None:
        self.root = Path(root)
        self._meta: Dict[str, Any] = meta or {}

    # ---------------------------------------------------------------- 生命周期

    @classmethod
    def create(
        cls,
        root: Path,
        title: str = "未命名小说",
        language: str = "zh",
        pov: str = "第三人称限知",
        tense: str = "过去时",
        max_rounds: int = 6,
        auto_fix: bool = True,
        target_words: int = 2500,
    ) -> "Project":
        root = Path(root)
        for sub in ("brief/characters", "chapters", "memory/summaries", ".novel"):
            (root / sub).mkdir(parents=True, exist_ok=True)

        meta: Dict[str, Any] = {
            "title": title,
            "language": language,
            "pov": pov,
            "tense": tense,
            "max_rounds": max_rounds,
            "auto_fix": auto_fix,
            "default_target_words": target_words,
            "created_at": _now(),
            "updated_at": _now(),
            "chapters": [],
        }
        project = cls(root, meta)
        project._write_templates()
        project.save()
        return project

    @classmethod
    def load(cls, root: Path) -> "Project":
        root = Path(root)
        meta_path = root / META_NAME
        if not meta_path.is_file():
            raise StoreError("这里不是小说项目（缺少 %s）：%s" % (META_NAME, root))
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise StoreError("project.json 不是合法 JSON：%s" % exc) from exc
        return cls(root, meta)

    def exists(self) -> bool:
        return (self.root / META_NAME).is_file()

    def save(self) -> None:
        self._meta["updated_at"] = _now()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / META_NAME).write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ---------------------------------------------------------------- 元数据

    def meta(self) -> Dict[str, Any]:
        return self._meta

    def update_meta(self, **fields: Any) -> None:
        self._meta.update(fields)
        self.save()

    # ---------------------------------------------------------------- 章节

    def chapters(self) -> List[Chapter]:
        return [Chapter.from_dict(item) for item in self._meta.get("chapters", [])]

    def chapter(self, number: int) -> Chapter:
        for chapter in self.chapters():
            if chapter.number == number:
                return chapter
        raise StoreError("第 %d 章不存在，先运行 novel plan 生成大纲" % number)

    def save_chapter(self, chapter: Chapter) -> None:
        items = self._meta.setdefault("chapters", [])
        for index, item in enumerate(items):
            if int(item.get("number", 0)) == chapter.number:
                items[index] = chapter.to_dict()
                break
        else:
            items.append(chapter.to_dict())
        self.save()

    def set_chapters(self, chapters: Iterable[Chapter]) -> None:
        self._meta["chapters"] = [chapter.to_dict() for chapter in chapters]
        self.save()

    # ---------------------------------------------------------------- 设定

    def brief_path(self, name: str) -> Path:
        return self.root / "brief" / (name + ".md")

    def brief_text(self, name: str) -> str:
        path = self.brief_path(name)
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def write_brief(self, name: str, text: str) -> None:
        path = self.brief_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")

    def characters(self) -> Dict[str, str]:
        directory = self.root / "brief" / "characters"
        if not directory.is_dir():
            return {}
        result: Dict[str, str] = {}
        for path in sorted(directory.glob("*.md")):
            if path.stem.startswith("_"):
                continue
            text = path.read_text(encoding="utf-8").strip()
            if text:
                result[path.stem] = text
        return result

    # ---------------------------------------------------------------- 记忆

    def canon(self) -> str:
        path = self.root / "memory" / "canon.md"
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def append_canon(self, facts: Iterable[str]) -> None:
        cleaned = [str(fact).strip() for fact in facts if str(fact).strip()]
        if not cleaned:
            return
        path = self.root / "memory" / "canon.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8").rstrip() if path.is_file() else CANON_HEADER.rstrip()
        lines = [existing, ""] + ["- " + fact for fact in cleaned]
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def summary_path(self, number: int) -> Path:
        return self.root / "memory" / "summaries" / ("ch%02d.md" % number)

    def summary(self, number: int) -> str:
        path = self.summary_path(number)
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def write_summary(self, number: int, text: str) -> None:
        path = self.summary_path(number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n", encoding="utf-8")

    def summaries(self, numbers: Iterable[int]) -> str:
        blocks = []
        for number in numbers:
            text = self.summary(number)
            if text:
                blocks.append("第 %d 章：%s" % (number, text))
        return "\n\n".join(blocks)

    # ---------------------------------------------------------------- 正文

    def chapter_dir(self, number: int) -> Path:
        return self.root / "chapters" / ("ch%02d" % number)

    def draft_path(self, number: int) -> Path:
        return self.chapter_dir(number) / "draft.md"

    def draft(self, number: int) -> str:
        path = self.draft_path(number)
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def revision_path(self, number: int, revision: int) -> Path:
        return self.chapter_dir(number) / "revisions" / ("%04d.md" % revision)

    def revisions(self, number: int) -> List[int]:
        directory = self.chapter_dir(number) / "revisions"
        if not directory.is_dir():
            return []
        return sorted(int(path.stem) for path in directory.glob("*.md") if path.stem.isdigit())

    def read_revision(self, number: int, revision: int) -> str:
        path = self.revision_path(number, revision)
        if not path.is_file():
            raise StoreError("第 %d 章第 %d 稿不存在" % (number, revision))
        return path.read_text(encoding="utf-8").strip()

    def write_revision(self, chapter: Chapter, text: str) -> int:
        chapter.current_revision += 1
        directory = self.chapter_dir(chapter.number)
        (directory / "revisions").mkdir(parents=True, exist_ok=True)
        payload = text.strip() + "\n"
        self.revision_path(chapter.number, chapter.current_revision).write_text(
            payload, encoding="utf-8"
        )
        self.draft_path(chapter.number).write_text(payload, encoding="utf-8")
        self.save_chapter(chapter)
        return chapter.current_revision

    def restore_revision(self, number: int, revision: int) -> None:
        text = self.read_revision(number, revision)
        chapter = self.chapter(number)
        chapter.current_revision = revision
        self.draft_path(number).write_text(text + "\n", encoding="utf-8")
        self.save_chapter(chapter)

    def save_critique(self, number: int, revision: int, payload: Any) -> None:
        directory = self.chapter_dir(number) / "critique"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ("%04d.json" % revision)).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def critique(self, number: int, revision: Optional[int] = None) -> Optional[Dict[str, Any]]:
        directory = self.chapter_dir(number) / "critique"
        if not directory.is_dir():
            return None
        if revision is None:
            files = sorted(directory.glob("*.json"))
            if not files:
                return None
            target = files[-1]
        else:
            target = directory / ("%04d.json" % revision)
        if not target.is_file():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            return None

    # ---------------------------------------------------------------- 反馈

    def add_feedback(self, number: int, text: str, kind: str = "note") -> None:
        directory = self.chapter_dir(number)
        directory.mkdir(parents=True, exist_ok=True)
        entry = {"at": _now(), "kind": kind, "text": text}
        with (directory / "feedback.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def feedbacks(self, number: int) -> List[Dict[str, Any]]:
        path = self.chapter_dir(number) / "feedback.jsonl"
        if not path.is_file():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    # ---------------------------------------------------------------- 日志

    def log(self, event: str, **fields: Any) -> None:
        path = self.root / ".novel" / "log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": _now(), "event": event}
        payload.update(fields)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- 模板

    def _write_templates(self) -> None:
        templates = {
            "brief/background.md": BACKGROUND_TEMPLATE,
            "brief/premise.md": PREMISE_TEMPLATE,
            "brief/style.md": STYLE_TEMPLATE,
            "brief/characters/_模板.md": CHARACTER_TEMPLATE,
            "memory/canon.md": CANON_HEADER,
        }
        for rel, text in templates.items():
            path = self.root / rel
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text.rstrip() + "\n", encoding="utf-8")


BACKGROUND_TEMPLATE = """# 背景设定

（随手记就行，不用组织成文——AI 会依据这里的东西工作。留空的部分它不会乱猜。）

- 世界 / 时代：
- 主要地点：
- 社会规则或特殊设定：
- 已有意象、氛围参考：
"""

PREMISE_TEMPLATE = """# 核心前提与情节概要

## 一句话故事
（谁，在什么处境下，因为什么，要做什么，代价是什么）

## 主线（可粗略，1~5 条）
1.
2.

## 我希望的结局或方向
（也可以直接写：还没想好，想和 AI 一起定）

## 绝对不要出现的东西
-
"""

STYLE_TEMPLATE = """# 风格指令

- 语言：中文
- 视角：第三人称限知
- 时态：过去时
- 句子偏好：（短句克制 / 长句绵密 / 长短交替）
- 参考作家或作品：（说清楚参考的是什么，例如"汪曾祺的白描，不要他的题材"）
- 对话比例：（多 / 少 / 每场戏至少一次交锋）
- 禁忌：（例如：不要总结式抒情；不要"他知道，他明白"式心理直述；不要文绉绉的对话）
"""

CHARACTER_TEMPLATE = """# 人物：<名字>

- 身份：
- 外貌与习惯：
- 核心欲望：
- 恐惧 / 弱点：
- 说话方式（口头禅、句长、用词层次）：
- 与其他人的关系：
- 会做的事 / 绝不会做的事：
"""
