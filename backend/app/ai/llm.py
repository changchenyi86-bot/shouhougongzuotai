"""大模型调用接口。回复建议不绑定某一家模型，通过 LLM_PROVIDER 切换：

- claude：Anthropic 官方 SDK，密钥读环境变量 ANTHROPIC_API_KEY
- openai_compatible：任意兼容 OpenAI Chat Completions 协议的接口（如公司「售后数字人」的模型网关），
  配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

新增其他协议时实现 LLMProvider 并在 get_provider() 里注册即可。

除了回复建议（draft），和 AI 对话润色、回流判断也走同一个模型，用 generate() 按给定的结构输出。
"""

import json
import re
import time
from typing import Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.ai import usage
from app.config import settings


T = TypeVar("T", bound=BaseModel)


class SuggestionDraft(BaseModel):
    needs_reply: bool = Field(default=True, description="这条消息是否需要技术支持回复；寒暄、致谢、确认收到、测试等为 false")
    can_answer: bool = Field(description="是否有把握直接回答；false 表示建议转人工")
    confidence: Literal["high", "medium", "low"]
    reply: str = Field(description="可直接发到群里的回复草稿")
    reason: str = Field(description="一句话说明判断依据，或转人工时需要人工补充什么")
    references: list[str] = Field(description="回复实际依据的参考资料编号，如 [\"K1\", \"K3\"]；没有依据参考资料时为空列表")


class LLMError(Exception):
    pass


class LLMFormatError(LLMError):
    """模型回了，但输出不是要求的格式（没有 JSON、字段不对）。偶发，重问一次通常就好。"""


ImageInput = tuple[str, str]  # (说明文字, data URL)，如 ("图片1：客户 11-27 15:17 发的截图", "data:image/jpeg;base64,...")


class LLMProvider(Protocol):
    def draft(self, system: str, prompt: str, images: list[ImageInput] | None = None) -> tuple[SuggestionDraft, str]:
        """返回 (建议, 实际使用的模型名)。images 是附在提示词后面的原图。失败抛 LLMError。"""

    def generate(self, system: str, prompt: str, schema: type[T]) -> tuple[T, str]:
        """按 schema 的结构输出，返回 (结果, 实际使用的模型名)。失败抛 LLMError。"""


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

    def draft(self, system: str, prompt: str, images: list[ImageInput] | None = None) -> tuple[SuggestionDraft, str]:
        return self.generate(system, prompt, SuggestionDraft, images)

    def generate(self, system: str, prompt: str, schema: type[T], images: list[ImageInput] | None = None) -> tuple[T, str]:
        import anthropic

        purpose, started = usage.purpose_for(schema), time.monotonic()
        content: str | list = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for label, url in images:
                mime, data = url.removeprefix("data:").split(";base64,", 1)
                content += [{"type": "text", "text": label},
                            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}]

        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # 主模型因安全策略拒答时，服务端自动换模型重试
                output_config={"effort": self.effort},
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
            )
        except anthropic.APIError as exc:
            usage.record(purpose, self.model, ok=False, duration_ms=_ms(started), error=str(exc))
            raise LLMError(f"调用 Claude 失败：{exc}") from exc
        u = response.usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        usage.record(purpose, response.model,
                     (u.input_tokens or 0) + cache_read + (getattr(u, "cache_creation_input_tokens", 0) or 0),
                     u.output_tokens or 0, cache_read, duration_ms=_ms(started))
        if response.stop_reason == "refusal":
            raise LLMError("模型拒绝回答，请人工处理")
        if response.parsed_output is None:
            raise LLMError(f"模型输出无法解析（stop_reason={response.stop_reason}）")
        return response.parsed_output, response.model


JSON_INSTRUCTION = (
    "\n\n只输出一个 JSON 对象，不要输出其他内容，字段为："
    '{"needs_reply": true/false, "can_answer": true/false, "confidence": "high"/"medium"/"low", "reply": "回复草稿", '
    '"reason": "判断依据", "references": ["回复依据的参考资料编号，如 K1；没有就是空列表"]}'
)


def json_instruction(schema: type[BaseModel]) -> str:
    """让不支持结构化输出的模型按字段输出 JSON。"""
    if schema is SuggestionDraft:
        return JSON_INSTRUCTION
    props = schema.model_json_schema().get("properties", {})
    fields = []
    for name, p in props.items():
        allowed = p.get("enum")
        if allowed is None and "anyOf" in p:
            allowed = next((x.get("enum") for x in p["anyOf"] if "enum" in x), None)
        hint = p.get("description", "")
        if p.get("type") == "boolean":
            hint += "（true / false）"
        if allowed:
            hint += "（取值：" + " / ".join(map(str, allowed)) + "）"
        fields.append(f'"{name}": {hint}')
    return "\n\n只输出一个 JSON 对象，不要输出其他内容，字段为：\n" + "\n".join(fields)


def _json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise LLMFormatError("模型没有返回 JSON")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise LLMFormatError(f"模型输出格式不正确：{exc}") from exc
    if not isinstance(data, dict):
        raise LLMFormatError("模型输出格式不正确：不是 JSON 对象")
    return data


def parse_draft(text: str) -> SuggestionDraft:
    """从模型文本里取出 JSON（兼容 ```json 代码块包裹）。"""
    data = _json_object(text)
    # 自有模型不一定按要求给出 references，缺了或格式不对按「没有引用」处理
    refs = data.get("references")
    data["references"] = [str(r) for r in refs] if isinstance(refs, list) else []
    try:
        return SuggestionDraft.model_validate(data)
    except ValidationError as exc:
        raise LLMFormatError(f"模型输出格式不正确：{exc}") from exc


def parse_json(text: str, schema: type[T]) -> T:
    if schema is SuggestionDraft:
        return parse_draft(text)  # type: ignore[return-value]
    try:
        return schema.model_validate(_json_object(text))
    except ValidationError as exc:
        raise LLMFormatError(f"模型输出格式不正确：{exc}") from exc


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str, json_mode: bool = True, timeout: float = 60,
                 client: httpx.Client | None = None):
        if not base_url:
            raise LLMError("未配置 LLM_BASE_URL")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.json_mode = api_key, model, json_mode
        self._client = client or httpx.Client(timeout=timeout)

    def draft(self, system: str, prompt: str, images: list[ImageInput] | None = None) -> tuple[SuggestionDraft, str]:
        return self.generate(system, prompt, SuggestionDraft, images)

    def generate(self, system: str, prompt: str, schema: type[T], images: list[ImageInput] | None = None) -> tuple[T, str]:
        content: str | list = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for label, url in images:
                content += [{"type": "text", "text": label}, {"type": "image_url", "image_url": {"url": url}}]
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system + json_instruction(schema)},
                         {"role": "user", "content": content}],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        purpose = usage.purpose_for(schema)
        for attempt in range(2):  # 输出格式不对时重问一次（评测里约百分之一的回复是这样）
            started, data = time.monotonic(), None
            try:
                r = self._client.post(self.url, json=body, headers=headers)
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                record_openai(purpose, self.model, data, body["messages"], None, started, error=str(exc))
                raise LLMError(f"调用模型接口失败：{exc}") from exc
            record_openai(purpose, data.get("model") or self.model, data, body["messages"], text, started)
            try:
                return parse_json(text, schema), data.get("model") or self.model
            except LLMFormatError:
                if attempt:
                    raise
        raise AssertionError("unreachable")


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def record_openai(purpose: str, model: str, data, messages: list, text: str | None, started: float,
                  error: str | None = None) -> None:
    """记下一次 OpenAI 兼容接口调用的 token；接口没返回 usage 时按字数估。"""
    tokens = usage.openai_tokens(data.get("usage") if isinstance(data, dict) else None)
    if tokens is None and error is None:
        prompt = "".join(m["content"] if isinstance(m.get("content"), str)
                         else "".join(p.get("text", "") for p in m.get("content") or [] if isinstance(p, dict))
                         for m in messages)  # 附图时只估文字部分
        tokens, estimated = (usage.estimate(prompt), usage.estimate(text or ""), 0), True
    else:
        estimated = False
    i, o, c = tokens or (0, 0, 0)
    usage.record(purpose, model, i, o, c, ok=error is None, estimated=estimated, duration_ms=_ms(started),
                 error=error)


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
