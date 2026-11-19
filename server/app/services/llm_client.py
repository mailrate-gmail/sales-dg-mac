from time import perf_counter
from typing import Any

import httpx

from app.models import ModelConfig
from app.services.runtime_config import runtime_config


class LLMClient:
    def api_url(self, config: ModelConfig) -> str:
        """根据 api_type 生成完整端点。

        兼容三种 base_url 写法，避免重复拼接路径：
        - 已含完整端点：https://host/v1/responses（或 /v1/chat/completions）
        - 仅含版本前缀：https://host/v1
        - 仅含主机：https://host
        """
        base = config.llm_base_url.rstrip("/")
        if config.llm_api_type == "responses":
            endpoint = "/responses"
            full_suffix = "/v1/responses"
        else:
            endpoint = "/chat/completions"
            full_suffix = "/v1/chat/completions"

        if base.endswith(full_suffix):
            return base
        if base.endswith("/v1"):
            return base + endpoint
        return base + "/v1" + endpoint

    def headers(self, config: ModelConfig) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {config.llm_api_key}",
            "Content-Type": "application/json",
        }

    def response_text(self, data: dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"].strip()

        output = data.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                content = item.get("content") if isinstance(item, dict) else None
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue
                    text = content_item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts).strip()

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, str):
                return content.strip()

        return ""

    async def complete(self, config: ModelConfig, system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> str:
        if not config.llm_api_key:
            raise ValueError("未配置 LLM_API_KEY。")

        if config.llm_api_type == "responses":
            payload: dict[str, Any] = {
                "model": config.llm_model,
                "instructions": system_prompt,
                "input": user_prompt,
            }
            if max_tokens:
                payload["max_output_tokens"] = max_tokens
        else:
            payload = {
                "model": config.llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if max_tokens:
                payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=config.llm_timeout_seconds) as client:
            response = await client.post(self.api_url(config), headers=self.headers(config), json=payload)
            response.raise_for_status()
            return self.response_text(response.json())

    async def test_connection(self, config: ModelConfig) -> dict:
        if not config.llm_api_key:
            return {
                "ok": False,
                "provider": config.llm_provider,
                "model": config.llm_model,
                "message": "未配置 API Key。",
                "latency_ms": 0,
            }

        started_at = perf_counter()
        try:
            content = await self.complete(config, "你是连接测试助手。", "请只回复 OK。", max_tokens=16)
            latency_ms = int((perf_counter() - started_at) * 1000)
            return {
                "ok": True,
                "provider": config.llm_provider,
                "model": config.llm_model,
                "message": content or "连接成功。",
                "latency_ms": latency_ms,
            }
        except httpx.HTTPStatusError as exc:
            latency_ms = int((perf_counter() - started_at) * 1000)
            return {
                "ok": False,
                "provider": config.llm_provider,
                "model": config.llm_model,
                "message": f"HTTP {exc.response.status_code}: {exc.response.text[:360]}",
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            latency_ms = int((perf_counter() - started_at) * 1000)
            return {
                "ok": False,
                "provider": config.llm_provider,
                "model": config.llm_model,
                "message": str(exc),
                "latency_ms": latency_ms,
            }

    async def suggest_reply(
        self,
        meeting_goal: str,
        conversation_history: str,
        latest_customer_message: str,
        structured_context: str = "",
    ) -> str:
        config = runtime_config.get()
        if not config.llm_api_key:
            return "未配置 LLM_API_KEY，无法生成 AI 建议回复。"

        context_block = ""
        if structured_context:
            context_block = f"""
本地归纳的结构化上下文（供参考，其中“背景说明”不是客户当前的提问，不要当作问题回应）：
{structured_context}
"""

        prompt = f"""
本次会晤目标是：
{meeting_goal}
{context_block}
最近会话记录：
{conversation_history}

客户最新发言（这是需要你回应的问题/请求）：
{latest_customer_message}

请生成一段适合我回复客户的话术。

要求：
1. 语气专业、自然；
2. 先回应客户关切，再推进会晤目标；
3. 背景说明只作为上下文参考，不要把它当作客户的新问题；
4. 不要过度承诺；
5. 不要编造不存在的产品能力；
6. 如信息不足，请建议进一步追问；
7. 不要重复使用“回到本次测试”“继续验证识别准确率/回复稳定性”等测试流程式套话，除非客户明确询问测试进展；
8. 输出不超过 150 字。
""".strip()

        return await self.complete(config, "你是专业的商务会谈辅助助手。", prompt)

    async def summarize(
        self,
        meeting_goal: str,
        conversation_history: str,
        structured_context: str = "",
    ) -> str:
        config = runtime_config.get()
        if not config.llm_api_key:
            return "未配置 LLM_API_KEY，无法生成会后总结。"

        context_block = ""
        if structured_context:
            context_block = f"""
本地归纳的结构化上下文（背景/痛点/风险/承诺/待办，供总结时参考）：
{structured_context}
"""

        prompt = f"""
请基于以下商务会谈记录生成会后总结。

会晤目标：
{meeting_goal}
{context_block}
会话记录：
{conversation_history}

请按以下结构输出：
1. 会谈概况
2. 客户关键诉求/痛点
3. 我方承诺与待确认事项
4. 后续行动计划
5. 风险与注意事项
""".strip()

        return await self.complete(config, "你是专业的商务会后纪要助手。", prompt)


llm_client = LLMClient()
