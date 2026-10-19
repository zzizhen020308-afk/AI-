"""提示词加载：提示词是普通 markdown 文件，作者可以直接改，不用碰代码。

文件格式：
    ::: system
    系统提示词
    ::: user
    用户提示词，可用 {{变量}} 占位

查找顺序（先命中先用）：
    1. 环境变量 NOVEL_PROMPT_DIR 指向的目录
    2. <项目根>/prompts/
    3. 仓库内 prompts/
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
SECTION = re.compile(r"^:::\s*(system|user)\s*$", re.MULTILINE)

PROMPT_NAMES = ("intake", "outline", "draft", "revise", "critique", "accept")


class PromptError(RuntimeError):
    """提示词文件缺失或格式不对。"""


class PromptStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._cache: Dict[str, Tuple[str, str]] = {}

    @classmethod
    def locate(cls, project_root: Optional[Path] = None) -> "PromptStore":
        candidates = []
        override = os.environ.get("NOVEL_PROMPT_DIR")
        if override:
            candidates.append(Path(override))
        if project_root is not None:
            candidates.append(Path(project_root) / "prompts")
        candidates.append(Path(__file__).resolve().parents[1] / "prompts")
        for candidate in candidates:
            if candidate.is_dir() and any(candidate.glob("*.md")):
                return cls(candidate)
        raise PromptError("找不到提示词目录，请检查 prompts/ 是否存在")

    def path(self, name: str) -> Path:
        return self.directory / (name + ".md")

    def render(self, name: str, values: Mapping[str, str]) -> Tuple[str, str]:
        system, user = self.load(name)
        return self._fill(system, values), self._fill(user, values)

    def load(self, name: str) -> Tuple[str, str]:
        if name in self._cache:
            return self._cache[name]
        path = self.path(name)
        if not path.is_file():
            raise PromptError("缺少提示词文件：%s" % path)
        text = path.read_text(encoding="utf-8")
        parts = SECTION.split(text)
        sections: Dict[str, str] = {}
        # split 结果为 [前言, 标签, 正文, 标签, 正文, ...]
        for index in range(1, len(parts) - 1, 2):
            sections[parts[index].strip().lower()] = parts[index + 1].strip()
        if "system" not in sections or "user" not in sections:
            raise PromptError("%s 需要同时包含 '::: system' 与 '::: user' 两段" % path.name)
        self._cache[name] = (sections["system"], sections["user"])
        return self._cache[name]

    @staticmethod
    def _fill(template: str, values: Mapping[str, str]) -> str:
        return PLACEHOLDER.sub(
            lambda match: str(values.get(match.group(1), match.group(0))), template
        )
