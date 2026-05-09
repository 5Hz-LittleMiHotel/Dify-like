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
        # 函数作用：把项目内部的 AgentInvocation 转成 AgentScope 调用，再把 AgentScope 的输出转回项目统一的 RuntimeEvent。
        try:
            # 1.创建 AgentScope agent
            agent = self._create_agent(invocation)
            # 2.导入 AgentScope 的消息和流式工具:
            from agentscope.message import Msg  # Msg 用来把用户输入包装成 AgentScope 能识别的消息
            from agentscope.pipeline import stream_printing_messages  # stream_printing_messages 用来接收 AgentScope 运行中的流式输出。
        except Exception as exc:
            yield {
                "type": "adapter_error",
                "adapter": self.name,
                "message": str(exc),
            }
            return
        # 3.调用 AgentScope agent, 真正把用户问题交给 AgentScope
        agent.set_console_output_enabled(False)  # 关闭 AgentScope 自己往控制台打印
        task = agent(Msg("user", invocation.query, "user"))  # 把用户输入包装成一条 user 消息, 启动 AgentScope 的 ReActAgent 执行, task 会被交给 stream_printing_messages() 去流式消费
        previous = ""
        # 4.把 AgentScope 输出(Msg流)转换成项目事件(RuntimeEvent流), 是 adapter 的核心：
        """
        AgentScope 给出的可能是“当前完整文本”，而前端需要的是“增量文本”。
        所以代码用 previous 记录上一次文本，然后算出这次新增的部分 delta。
        然后它把 AgentScope 的输出转成项目统一事件：

        有新增内容 -> yield message_delta
        最后一条消息 -> yield final

        上层 WorkflowExecutor 和 chat_stream() 不需要知道 AgentScope 的内部细节，只要继续处理这些统一事件即可。
        """
        # 一边运行 task，一边捕获 AgentScope agent 打印出来的流式消息：
        # 其中last是一个bool，表示是否为当前流式消息的最后一块。
        # 当前 MVP 用它来触发final，可以跑通简单场景；后面真实接 ReAct、工具、多段消息时，可以再检查它是否会过早触发 final
        async for msg, last in stream_printing_messages(agents=[agent], coroutine_task=task):
            current = msg.get_text_content()
            # delta 是从累计文本里切出来的新增文本片段。前端只需要不断 append delta，就能形成流式输出。
            delta = current[len(previous) :] if current.startswith(previous) else current
            previous = current
            if delta:
                yield {"type": "message_delta", "content": delta}
            if last:
                yield {"type": "final", "content": current}

    def _create_agent(self, invocation: AgentInvocation):
        """
        创建模型
        创建 formatter
        创建 Toolkit
        注册平台工具
        拼 system prompt
        创建 ReActAgent
        """
        from agentscope.agent import ReActAgent
        from agentscope.memory import InMemoryMemory
        from agentscope.tool import Toolkit

        model, formatter = self._build_model_and_formatter(invocation)  # formatter 是 LLM 消息协议适配器，告诉 AgentScope 怎么把消息整理给这个品牌的LLM模型
        toolkit = Toolkit()  # 创建空工具箱
        for tool_name in invocation.enabled_tools:  # 把当前 app 启用的工具注册进工具箱，这些平台工具就会被包装成 AgentScope 可以调用的函数
            toolkit.register_tool_function(self._make_agentscope_tool(tool_name))
            # register_tool_function() 虽然源码很长，但核心就一句话：
            # 把一个 Python 函数解析成 AgentScope 能理解的工具对象，然后存进 toolkit.tools 字典里。

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
        # 函数的作用是把本平台里的工具名，包装成一个 AgentScope 可以注册和调用的 Python 函数。
        def platform_tool(**kwargs: Any):
            # 这个 platform_tool 就是最终注册进 AgentScope Toolkit 的工具函数（当 AgentScope agent 决定调用工具时，实际调用就是platform_tool(kwargs)）
            """Run a platform managed tool.

            Args:
                kwargs: Tool arguments generated by the agent.
            """
            from agentscope.message import TextBlock
            from agentscope.tool import ToolResponse

            # 函数定义里是**kwargs, 所以调用本项目工具的这些参数会被收集成字典: kwargs = {"key": "value"}; 然后下面会调用本项目自己的工具系统, 返回一个dict格式结果
            result = run_tool(tool_name, kwargs)
            # 把上一行代码的结果转成 AgentScope 认识的工具响应格式: 先包成 TextBlock, 再包成 ToolResponse, 返回给 AgentScope agent
            return ToolResponse(content=[TextBlock(type="text", text=str(result))])
        
        # 在 Python 里，函数也是对象。这里修改包装过的函数对象的名称。否则默认为platform_tool，则加入到toolkit后所有工具函数都叫 platform_tool
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
