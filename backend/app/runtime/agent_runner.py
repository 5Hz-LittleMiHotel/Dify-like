import re
from collections.abc import AsyncIterator
from typing import Any

from app.tools.registry import run_tool


class AgentRunner:
    """Small local runner used to keep the platform demo runnable without an LLM key."""

    async def run(
        self,
        query: str,
        system_prompt: str,
        enabled_tools: list[str],
        retrieved_chunks: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        del system_prompt

        answer_parts = []

        order_match = re.search(r"\b(\d{4,})\b", query)
        if order_match and "query_order" in enabled_tools:
            order_id = order_match.group(1)
            tool_result = run_tool("query_order", {"order_id": order_id})
            yield {
                "type": "tool_call",
                "name": "query_order",
                "input": {"order_id": order_id},
                "output": tool_result,
            }
            answer_parts.append(str(tool_result.get("status", tool_result)))

        if any(word in query for word in ["几点", "时间", "现在"]) and "current_time" in enabled_tools:
            tool_result = run_tool("current_time", {})
            yield {"type": "tool_call", "name": "current_time", "input": {}, "output": tool_result}
            answer_parts.append(f"当前时间是 {tool_result['result']}。")

        if any(word in query for word in ["天气", "气温"]) and "mock_weather" in enabled_tools:
            city = "上海"
            tool_result = run_tool("mock_weather", {"city": city})
            yield {"type": "tool_call", "name": "mock_weather", "input": {"city": city}, "output": tool_result}
            answer_parts.append(tool_result["weather"])

        if retrieved_chunks:
            context = retrieved_chunks[0]["content"]
            answer_parts.append(f"根据知识库：{context[:260]}")

        if not answer_parts:
            answer_parts.append("我已经收到你的问题。当前 demo 使用本地 ReAct-like runner，接入 AgentScope 后会由真实 agent 生成回答。")

        final_answer = "\n\n".join(answer_parts)
        for token in final_answer:
            yield {"type": "message_delta", "content": token}
        yield {"type": "final", "content": final_answer}
