"""大模型调用接口。回复建议不绑定某一家模型，通过 LLM_PROVIDER 切换：

- claude：Anthropic 官方 SDK，密钥读环境变量 ANTHROPIC_API_KEY
- openai_compatible：任意兼容 OpenAI Chat Completions 协议的接口（如公司「售后数字人」的模型网关），
  配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

新增其他协议时实现 LLMProvider 并在 get_provider() 里注册即可。
"""

import json
import re
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import settings


class SuggestionDraft(BaseModel):
    can_answer: bool = Field(description="是否有把握直接回答；false 表示建议转人工")
    confidence: Literal["high", "medium", "low"]
    reply: str = Field(description="可直接发到群里的回复草稿")
    reason: str = Field(description="一句话说明判断依据，或转人工时需要人工补充什么")
    references: list[str] = Field(description="回复实际依据的参考资料编号，如 [\"K1\", \"K3\"]；没有依据参考资料时为空列表")


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    def draft(self, system: str, prompt: str) -> tuple[SuggestionDraft, str]:
        """返回 (建议, 实际使用的模型名)。失败抛 LLMError。"""


class ClaudeProvider:
    def __init__(self, model: str, effort: str):
        self.model, self.effort = model, effort
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()  # 从环境变量 ANTHROPIC_API_KEY 读取密钥
        return self._client

    def draft(self, system: str, prompt: str) -> tuple[SuggestionDraft, str]:
        import anthropic

        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # 主模型因安全策略拒答时，服务端自动换模型重试
                output_config={"effort": self.effort},
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=SuggestionDraft,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"调用 Claude 失败：{exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("模型拒绝回答，请人工处理")
        if response.parsed_output is None:
            raise LLMError(f"模型输出无法解析（stop_reason={response.stop_reason}）")
        return response.parsed_output, response.model


JSON_INSTRUCTION = (
    "\n\n只输出一个 JSON 对象，不要输出其他内容，字段为："
    '{"can_answer": true/false, "confidence": "high"/"medium"/"low", "reply": "回复草稿", "reason": "判断依据", '
    '"references": ["回复依据的参考资料编号，如 K1；没有就是空列表"]}'
)


def parse_draft(text: str) -> SuggestionDraft:
    """从模型文本里取出 JSON（兼容 ```json 代码块包裹）。"""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise LLMError("模型没有返回 JSON")
    try:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            # 自有模型不一定按要求给出 references，缺了或格式不对按「没有引用」处理
            refs = data.get("references")
            data["references"] = [str(r) for r in refs] if isinstance(refs, list) else []
        return SuggestionDraft.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise LLMError(f"模型输出格式不正确：{exc}") from exc


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str, json_mode: bool = True, timeout: float = 60,
                 client: httpx.Client | None = None):
        if not base_url:
            raise LLMError("未配置 LLM_BASE_URL")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.json_mode = api_key, model, json_mode
        self._client = client or httpx.Client(timeout=timeout)

    def draft(self, system: str, prompt: str) -> tuple[SuggestionDraft, str]:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system + JSON_INSTRUCTION},
                         {"role": "user", "content": prompt}],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = self._client.post(self.url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"调用模型接口失败：{exc}") from exc
        return parse_draft(text), data.get("model") or self.model


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        if settings.llm_provider == "claude":
            _provider = ClaudeProvider(settings.claude_model, settings.claude_effort)
        elif settings.llm_provider == "openai_compatible":
            _provider = OpenAICompatibleProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                                                 settings.llm_json_mode, settings.llm_timeout)
        else:
            raise LLMError(f"不支持的 LLM_PROVIDER：{settings.llm_provider}")
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    global _provider
    _provider = provider
