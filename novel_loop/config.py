"""模型配置：全部来自环境变量（或工作目录下的 .env），不落盘密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

DEFAULTS: Dict[str, object] = {
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "temperature": 0.8,
    "max_tokens": 4096,
    "timeout": 120.0,
}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: float

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def describe(self) -> str:
        key = "未设置（mock 模式）" if not self.api_key else "已设置（%s…）" % self.api_key[:4]
        return "provider=%s model=%s base_url=%s key=%s" % (
            self.provider,
            self.model,
            self.base_url,
            key,
        )


def load_dotenv(path: Path) -> Dict[str, str]:
    """极简 .env 解析：KEY=VALUE，# 开头为注释。不覆盖已存在的环境变量。"""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def load_llm_config(
    env: Optional[Mapping[str, str]] = None, env_file: Optional[Path] = None
) -> LLMConfig:
    """读取配置。优先真实环境变量，其次 .env，最后内置默认值。

    未设置 NOVEL_API_KEY 时自动使用 mock provider，保证离线也能跑通完整流程。
    """
    merged: Dict[str, str] = {}
    if env_file is not None:
        merged.update(load_dotenv(env_file))
    merged.update(os.environ if env is None else env)  # type: ignore[arg-type]

    api_key = (merged.get("NOVEL_API_KEY") or "").strip()
    provider = (merged.get("NOVEL_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "openai-compatible" if api_key else "mock"

    def _num(env_key: str, default_key: str) -> float:
        raw = merged.get(env_key)
        if not raw:
            return float(DEFAULTS[default_key])  # type: ignore[arg-type]
        try:
            return float(raw)
        except ValueError:
            return float(DEFAULTS[default_key])  # type: ignore[arg-type]

    return LLMConfig(
        provider=provider,
        base_url=(merged.get("NOVEL_API_BASE") or str(DEFAULTS["base_url"])).rstrip("/"),
        api_key=api_key,
        model=(merged.get("NOVEL_MODEL") or str(DEFAULTS["model"])).strip(),
        temperature=_num("NOVEL_TEMPERATURE", "temperature"),
        max_tokens=int(_num("NOVEL_MAX_TOKENS", "max_tokens")),
        timeout=_num("NOVEL_TIMEOUT", "timeout"),
    )
