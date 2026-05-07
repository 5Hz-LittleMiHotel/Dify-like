import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.tools.registry import run_tool


RuntimeEvent = dict[str, Any]


@dataclass
class AgentInvocation:
    app_name: str
    query: str
    system_prompt: str
    model_provider: str
    model_name: str
    model_config: dict[str, Any] = field(default_factory=dict)
    node_config: dict[str, Any] = field(default_factory=dict)
    enabled_tools: list[str] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)


class BaseAgentAdapter:
    name = "base"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        raise NotImplementedError


class MockAgentAdapter(BaseAgentAdapter):
    name = "mock"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        answer_parts = []
        query = invocation.query
        enabled_tools = invocation.enabled_tools

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

        if invocation.retrieved_chunks:
            context = invocation.retrieved_chunks[0]["content"]
            answer_parts.append(f"根据知识库：{context[:260]}")

        if not answer_parts:
            answer_parts.append("我已经收到你的问题。当前 demo 使用 mock adapter；接入模型后会由 AgentScope 执行 agent。")

        final_answer = "\n\n".join(answer_parts)
        for token in final_answer:
            yield {"type": "message_delta", "content": token}
        yield {"type": "final", "content": final_answer}


class AgentScopeAdapter(BaseAgentAdapter):
    name = "agentscope"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        try:
            agent = self._create_agent(invocation)
            from agentscope.message import Msg
            from agentscope.pipeline import stream_printing_messages
        except Exception as exc:
            yield {
                "type": "adapter_error",
                "adapter": self.name,
                "message": str(exc),
            }
            return

        agent.set_console_output_enabled(False)
        task = agent(Msg("user", invocation.query, "user"))
        previous = ""

        async for msg, last in stream_printing_messages(agents=[agent], coroutine_task=task):
            current = msg.get_text_content()
            delta = current[len(previous) :] if current.startswith(previous) else current
            previous = current
            if delta:
                yield {"type": "message_delta", "content": delta}
            if last:
                yield {"type": "final", "content": current}

    def _create_agent(self, invocation: AgentInvocation):
        from agentscope.agent import ReActAgent
        from agentscope.memory import InMemoryMemory
        from agentscope.tool import Toolkit

        model, formatter = self._build_model_and_formatter(invocation)
        toolkit = Toolkit()
        for tool_name in invocation.enabled_tools:
            toolkit.register_tool_function(self._make_agentscope_tool(tool_name))

        sys_prompt = self._build_system_prompt(invocation)
        return ReActAgent(
            name=invocation.app_name or "assistant",
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit,
            memory=InMemoryMemory(),
        )

    def _build_model_and_formatter(self, invocation: AgentInvocation):
        provider = (invocation.model_config.get("provider") or invocation.model_provider).lower()
        model_name = invocation.model_config.get("model_name") or invocation.model_name
        api_key_env = invocation.model_config.get("api_key_env")
        base_url = invocation.model_config.get("base_url")

        if provider in {"openai", "openai_compatible", "deepseek", "vllm"}:
            from agentscope.formatter import OpenAIChatFormatter
            from agentscope.model import OpenAIChatModel

            kwargs: dict[str, Any] = {
                "model_name": model_name,
                "api_key": os.getenv(api_key_env or "OPENAI_API_KEY", ""),
                "stream": True,
            }
            if base_url:
                kwargs["base_url"] = base_url
            return OpenAIChatModel(**kwargs), OpenAIChatFormatter()

        if provider in {"dashscope", "qwen"}:
            from agentscope.formatter import DashScopeChatFormatter
            from agentscope.model import DashScopeChatModel

            return (
                DashScopeChatModel(
                    model_name=model_name,
                    api_key=os.getenv(api_key_env or "DASHSCOPE_API_KEY", ""),
                    stream=True,
                ),
                DashScopeChatFormatter(),
            )

        raise ValueError(f"Unsupported AgentScope model provider: {provider}")

    def _build_system_prompt(self, invocation: AgentInvocation) -> str:
        if not invocation.retrieved_chunks:
            return invocation.system_prompt
        context = "\n\n".join(chunk["content"] for chunk in invocation.retrieved_chunks)
        return f"{invocation.system_prompt}\n\nKnowledge context:\n{context}"

    def _make_agentscope_tool(self, tool_name: str):
        def platform_tool(**kwargs: Any):
            """Run a platform managed tool.

            Args:
                kwargs: Tool arguments generated by the agent.
            """
            from agentscope.message import TextBlock
            from agentscope.tool import ToolResponse

            result = run_tool(tool_name, kwargs)
            return ToolResponse(content=[TextBlock(type="text", text=str(result))])

        platform_tool.__name__ = tool_name
        return platform_tool


def build_agent_adapter(adapter_name: str | None, model_provider: str) -> BaseAgentAdapter:
    selected = (adapter_name or "").lower()
    provider = (model_provider or "").lower()
    if selected == "mock":
        return MockAgentAdapter()
    if selected == "agentscope":
        return AgentScopeAdapter()
    if provider not in {"", "mock"}:
        return AgentScopeAdapter()
    return MockAgentAdapter()
