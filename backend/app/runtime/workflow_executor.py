from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.runtime.agent_adapters import AgentInvocation, RuntimeEvent, build_agent_adapter
from app.services.rag_service import retrieve_chunks
from app.services.run_log_service import add_step
from app.tools.registry import run_tool


@dataclass
class WorkflowResult:
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)


class WorkflowExecutor:
    def __init__(self, db: Session, app: Any, run_id: str):
        self.db = db
        self.app = app
        self.run_id = run_id
        self.result = WorkflowResult()

    async def execute(self, query: str, enabled_tools: list[str]) -> AsyncIterator[RuntimeEvent]:
        context: dict[str, Any] = {
            "query": query,
            "enabled_tools": enabled_tools,
        }

        for node in self._ordered_nodes(self.app.workflow_spec):
            node_type = self._normalize_type(node.get("type", ""))
            if node_type == "start":
                for event in self._execute_start(node, context):
                    yield event
            elif node_type == "retrieval":
                for event in self._execute_retrieval(node, context):
                    yield event
            elif node_type == "tool":
                for event in self._execute_tool(node, context):
                    yield event
            elif node_type == "agent":
                async for event in self._execute_agent(node, context):
                    yield event
            elif node_type == "end":
                for event in self._execute_end(node, context):
                    yield event
            else:
                event = {
                    "type": "workflow_warning",
                    "node_id": node.get("id"),
                    "message": f"Unsupported node type: {node_type}",
                }
                add_step(self.db, self.run_id, "workflow_warning", node.get("id", "unknown"), node, event)
                yield event

    def _execute_start(self, node: dict[str, Any], context: dict[str, Any]):
        output = {"query": context["query"]}
        add_step(self.db, self.run_id, "start", node.get("id", "start"), node, output)
        yield {"type": "workflow_node", "node_id": node.get("id"), "node_type": "start", "output": output}

    def _execute_retrieval(self, node: dict[str, Any], context: dict[str, Any]):
        started = perf_counter()
        enabled = bool(node.get("enabled", True))
        chunks = retrieve_chunks(self.db, self.app.id, context["query"], limit=int(node.get("top_k", 3))) if enabled else []
        self.result.retrieved_chunks = chunks
        output = {
            "chunks": chunks,
            "enabled": enabled,
        }
        add_step(
            self.db,
            self.run_id,
            "retrieval",
            node.get("id", "retrieval"),
            {"query": context["query"], "node": node},
            output,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        yield {"type": "retrieval", **output}

    def _execute_tool(self, node: dict[str, Any], context: dict[str, Any]):
        started = perf_counter()
        tool_name = node.get("tool_name") or node.get("name")
        tool_input = node.get("input", {})
        output = run_tool(tool_name, tool_input)
        event = {
            "type": "tool_call",
            "name": tool_name,
            "input": tool_input,
            "output": output,
            "node_id": node.get("id"),
        }
        self.result.tool_calls.append(event)
        add_step(
            self.db,
            self.run_id,
            "tool_call",
            tool_name or node.get("id", "tool"),
            {"node": node, "context": {"query": context["query"]}},
            output,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        yield event

    async def _execute_agent(self, node: dict[str, Any], context: dict[str, Any]) -> AsyncIterator[RuntimeEvent]:
        # 记录 agent 节点开始时间
        started = perf_counter()
        # 读取节点级配置
        adapter_name = node.get("adapter")
        model_config = node.get("model", {})
        # 决定使用哪个 agent adapter
        adapter = build_agent_adapter(adapter_name, model_config.get("provider") or self.app.model_provider)
        # 构造 AgentInvocation, agent runtime 的输入包. 作用：WorkflowExecutor 把 workflow 前面准备好的东西打包，然后交给 agent adapter
        invocation = AgentInvocation(
            app_name=self.app.name,
            query=context["query"],
            system_prompt=self.app.system_prompt,
            model_provider=model_config.get("provider") or self.app.model_provider,
            model_name=model_config.get("model_name") or self.app.model_name,
            model_config=model_config,
            node_config=node,
            enabled_tools=context["enabled_tools"],
            retrieved_chunks=self.result.retrieved_chunks, # retrieval 节点检索到的片段在这里传给 agent
        )

        final_answer = ""
        # 跑 adapter，并处理事件
        async for event in adapter.run(invocation):
            if event["type"] == "tool_call":
                self.result.tool_calls.append(event)
                add_step(
                    self.db,
                    self.run_id,
                    "tool_call",
                    event["name"],
                    event.get("input", {}),
                    event.get("output", {}),
                )
            elif event["type"] == "final":
                final_answer = str(event.get("content", ""))
                self.result.answer = final_answer
            elif event["type"] == "adapter_error":
                add_step(self.db, self.run_id, "error", "agent_adapter", {"node": node}, event, error=event["message"])
            yield event
        # 最后，写一个 agent step，表示这个 agent 节点整体跑完了：
        add_step(
            self.db,
            self.run_id,
            "agent",
            node.get("id", "agent"),
            {"adapter": adapter.name, "model_provider": invocation.model_provider, "model_name": invocation.model_name},
            {"answer": final_answer, "tool_calls": self.result.tool_calls},
            latency_ms=int((perf_counter() - started) * 1000),
        )

    def _execute_end(self, node: dict[str, Any], context: dict[str, Any]):
        output = {
            "answer": self.result.answer,
            "tool_calls": self.result.tool_calls,
            "retrieved_chunks": self.result.retrieved_chunks,
        }
        add_step(self.db, self.run_id, "end", node.get("id", "end"), {"query": context["query"]}, output)
        yield {"type": "workflow_node", "node_id": node.get("id"), "node_type": "end", "output": output}

    def _ordered_nodes(self, workflow_spec: dict[str, Any] | None) -> list[dict[str, Any]]:
        spec = workflow_spec or {}
        nodes = {node["id"]: node for node in spec.get("nodes", []) if "id" in node}
        if not nodes:
            return [
                {"id": "start", "type": "start"},
                {"id": "retrieval", "type": "retrieval", "enabled": True},
                {"id": "agent", "type": "react_agent"},
                {"id": "end", "type": "end"},
            ]

        edges = spec.get("edges", [])
        next_by_source: dict[str, str] = {}
        for edge in edges:
            if isinstance(edge, list) and len(edge) == 2:
                next_by_source[edge[0]] = edge[1]
            elif isinstance(edge, dict) and edge.get("from") and edge.get("to"):
                next_by_source[edge["from"]] = edge["to"]

        current = "start" if "start" in nodes else next(iter(nodes))
        ordered = []
        seen = set()
        while current in nodes and current not in seen:
            seen.add(current)
            ordered.append(nodes[current])
            current = next_by_source.get(current, "")

        return ordered

    def _normalize_type(self, node_type: str) -> str:
        # 它不是在“完整标准化所有节点类型”，而是在处理当前唯一的同义词。其他类型要么已经是标准名，要么本来就该被当成未知节点。
        if node_type in {"agent", "react_agent"}:
            return "agent"
        return node_type
