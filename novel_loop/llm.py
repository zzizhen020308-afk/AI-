"""模型接入层：只依赖标准库，走 OpenAI 兼容的 /chat/completions。

任何兼容 OpenAI 协议的服务都能用：
    DeepSeek   https://api.deepseek.com/v1
    OpenAI     https://api.openai.com/v1
    Ollama     http://localhost:11434/v1
    vLLM / LM Studio / OpenRouter ...
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional, Protocol, runtime_checkable

from .config import LLMConfig


class LLMError(RuntimeError):
    """模型调用失败（网络、鉴权、返回格式）。"""


@runtime_checkable
class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str, *, task: str = "draft") -> str:
        ...


class OpenAICompatProvider:
    name = "openai-compatible"

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise LLMError("缺少 NOVEL_API_KEY，无法调用真实模型")
        self.config = config

    def complete(self, system: str, user: str, *, task: str = "draft") -> str:
        url = self.config.base_url + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.config.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise LLMError("模型接口返回 HTTP %s：%s" % (exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise LLMError("无法连接 %s：%s" % (url, getattr(exc, "reason", exc))) from exc
        except TimeoutError as exc:
            raise LLMError("调用 %s 超时（%ss）" % (url, self.config.timeout)) from exc

        try:
            data = json.loads(body)
            return str(data["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("模型响应格式不符合预期：%s" % body[:400]) from exc


class MockProvider:
    """离线 provider：按 task 返回结构合法的假数据，用于跑通流程与跑测试。"""

    name = "mock"

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config

    def complete(self, system: str, user: str, *, task: str = "draft") -> str:
        if task == "outline":
            return json.dumps(self._outline(user), ensure_ascii=False)
        if task == "intake":
            return json.dumps(
                {
                    "gaps": ["主角的外在目标还不够具体", "还没确定叙事的时间跨度"],
                    "questions": [
                        "主角最想要的东西，能不能用一个具体物件来指代？",
                        "这个故事你更希望读者记住哪一个瞬间？",
                    ],
                    "risks": ["背景设定偏多，容易变成设定说明而不是故事"],
                },
                ensure_ascii=False,
            )
        if task == "critique":
            return json.dumps(
                {
                    "overall": "（mock）整体可读，中段节奏偏平。",
                    "issues": [
                        {
                            "type": "pacing",
                            "severity": "minor",
                            "detail": "（mock）第二段缺少转折，读者容易掉出去。",
                            "fix": "在第二段末尾加一个意外发现，把场景推向下一拍。",
                        }
                    ],
                    "questions": [
                        "（mock）主角此刻最怕失去什么？",
                        "（mock）这一章结尾要不要留一个钩子？",
                    ],
                },
                ensure_ascii=False,
            )
        if task == "accept":
            return json.dumps(
                {
                    "summary": "（mock）本章确立了主角的处境与第一个疑点，为下一章埋下线索。",
                    "canon": [
                        "（mock）主角目前不清楚收据背面的字是谁写的。",
                        "（mock）故事发生在雨季的城市。",
                    ],
                    "open_threads": ["收据背面的字迹来源"],
                },
                ensure_ascii=False,
            )
        return self._prose(user, task)

    # -- helpers ---------------------------------------------------------

    def _prose(self, user: str, task: str) -> str:
        title = _extract(r"第\s*\d+\s*章《([^》]+)》", user) or "本章"
        feedback = _extract(r"【作者反馈[^\n]*】\s*\n(.{0,80})", user)
        header = "（mock 正文·%s：离线占位文本，只用来验证流程；配上 NOVEL_API_KEY 即为真实内容。）" % (
            "第 " + (_extract(r"第\s*(\d+)\s*章", user) or "?") + " 章《" + title + "》"
        )
        body = [
            header,
            "",
            "雨在凌晨三点停了，空气里全是铁锈味。他站在便利店门口，手里那张收据已经被汗浸软。"
            "他数了三遍，数字还是不对——不是多了，是少了，少得像是有人从他的人生里拿走了一段时间。",
            "",
            "收据背面有一行不是他写的字。字很小，笔画却压得很重，像写的人怕它被风吹走。",
            "",
            "他把收据翻过来，又翻回去。玻璃门映出他的脸，比出门时老了一点。",
        ]
        if task == "revise":
            body.insert(1, "")
            body.insert(
                2,
                "（修订稿：已按你的意见调整——%s）"
                % (feedback.strip()[:60] if feedback else "加强了结尾的钩子"),
            )
        return "\n".join(body)

    def _outline(self, user: str) -> dict:
        titles = [
            "收据",
            "第七个人",
            "旧照片",
            "回声",
            "交换",
            "断线",
            "空房间",
            "天亮之前",
            "另一边",
            "第二次雨",
        ]
        count = int(_extract(r"(\d+)\s*章", user) or "8")
        count = max(1, min(count, len(titles)))
        chapters = []
        for i in range(count):
            chapters.append(
                {
                    "title": titles[i],
                    "beats": [
                        "（mock）主角发现一个说不通的细节，试图忽略它",
                        "（mock）这个细节牵出一个旧人，双方各说了一半真话",
                        "（mock）主角做出一个不可逆的小选择，留下钩子",
                    ],
                }
            )
        return {"chapters": chapters}


def _extract(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def build_provider(config: LLMConfig) -> Provider:
    if config.is_mock:
        return MockProvider(config)
    return OpenAICompatProvider(config)
