from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import HumanTask, Message, Run
from app.mcp.protocol import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    MCP_PROTOCOL_VERSION,
    MCP_TOOL_EXECUTION_ERROR,
    MCP_WORKFLOW_NOT_PUBLISHED,
    JsonRpcError,
    build_jsonrpc_error,
    build_jsonrpc_result,
    parse_jsonrpc_request,
)
from app.services.chat_service import create_chat_execution
from app.services.execution_event_service import get_pending_human_task
from app.services.workflow_mcp_server_service import (
    get_public_workflow_mcp_server_by_slug,
    verify_workflow_mcp_server_token,
)


RUN_WORKFLOW_TOOL_NAME = "run_workflow"
GET_WORKFLOW_RUN_TOOL_NAME = "get_workflow_run"


async def handle_workflow_mcp_request(
    db: Session,
    server_slug: str,
    authorization: str | None,
    payload: Any, # JSON-RPC request body sent by external MCP client
    request_headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request_id = payload.get("id") if isinstance(payload, dict) else None
    try:
        request_id, method, params = parse_jsonrpc_request(payload)
    except JsonRpcError as exc:
        return 200, build_jsonrpc_error(request_id, exc.code, exc.message, exc.data)

    resolved = get_public_workflow_mcp_server_by_slug(db, server_slug)
    if not resolved:
        return 404, {"detail": "MCP server not found"}
    workflow_server, workflow, app = resolved
    if not workflow_server.enabled:
        return 404, {"detail": "MCP server not found"}
    if not verify_workflow_mcp_server_token(workflow_server, authorization):
        return 401, {"detail": "Invalid MCP bearer token"}

    with _mcp_server_span(request_headers, method, server_slug, request_id):
        try:
            if method == "initialize":
                return 200, build_jsonrpc_result(
                    request_id,
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": workflow_server.server_name,
                            "version": "0.1.0",
                        },
                    },
                )

            if method == "tools/list":
                return 200, build_jsonrpc_result(
                    request_id,
                    {
                        "tools": [
                            {
                                "name": RUN_WORKFLOW_TOOL_NAME,
                                "description": workflow_server.description
                                or f"Run the published workflow '{workflow.name}'.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "conversation_id": {"type": "string"},
                                    },
                                    "required": ["query"],
                                },
                            },
                            {
                                "name": GET_WORKFLOW_RUN_TOOL_NAME,
                                "description": "Get the current status or final result of a workflow run.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"run_id": {"type": "string"}},
                                    "required": ["run_id"],
                                },
                            },
                        ]
                    },
                )

            if method == "tools/call":
                return 200, await _handle_tools_call(db, request_id, workflow_server.server_slug, workflow, app, params)

            return 200, build_jsonrpc_error(request_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
        except JsonRpcError as exc:
            return 200, build_jsonrpc_error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            return 200, build_jsonrpc_error(request_id, JSONRPC_INTERNAL_ERROR, str(exc))


@contextmanager
def _mcp_server_span(
    request_headers: Mapping[str, str] | None,
    method: str,
    server_slug: str,
    request_id: Any,
) -> Iterator[None]:
    parent_context = TraceContextTextMapPropagator().extract(request_headers or {})
    tracer = trace.get_tracer("dify_like.mcp")
    with tracer.start_as_current_span(
        f"mcp.server {method}",
        context=parent_context,
        kind=SpanKind.SERVER,
        attributes={
            "rpc.system": "jsonrpc",
            "rpc.method": method,
            "mcp.server.slug": server_slug,
            "mcp.request.id": str(request_id or ""),
        },
    ):
        yield


async def _handle_tools_call(
    db: Session,
    request_id: Any,
    server_slug: str,
    workflow: Any,
    app: Any,
    params: dict[str, Any],
) -> dict[str, Any]:
    tool_name = str(params.get("name") or "").strip()
    arguments = params.get("arguments", {})
    if tool_name not in {RUN_WORKFLOW_TOOL_NAME, GET_WORKFLOW_RUN_TOOL_NAME}:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unsupported tool name: {tool_name or 'empty'}")
    if not isinstance(arguments, dict):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call arguments must be an object.")

    mcp_user_id = f"mcp:{server_slug}"
    if tool_name == GET_WORKFLOW_RUN_TOOL_NAME:
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "get_workflow_run requires run_id.")
        return _workflow_run_result(db, request_id, run_id, workflow.id, mcp_user_id)

    query = str(arguments.get("query") or "").strip()
    if not query:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call requires a non-empty query.")

    published_version = workflow.published_version
    if not published_version:
        raise JsonRpcError(MCP_WORKFLOW_NOT_PUBLISHED, "Workflow is not published.")

    conversation_id = str(arguments.get("conversation_id") or "").strip() or None
    result = create_chat_execution(
        db,
        app,
        workflow,
        published_version,
        query,
        mcp_user_id,
        conversation_id,
    )
    run_id = result["run_id"]
    deadline = asyncio.get_running_loop().time() + get_settings().mcp_tool_timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        db.expire_all()
        run = db.get(Run, run_id)
        if run and run.status in {"waiting_human", "interrupted", "success", "rejected", "error"}:
            break
        await asyncio.sleep(0.25)
    return _workflow_run_result(db, request_id, run_id, workflow.id, mcp_user_id)


def _workflow_run_result(
    db: Session,
    request_id: Any,
    run_id: str,
    workflow_id: str,
    mcp_user_id: str,
) -> dict[str, Any]:
    from app.db.models import Conversation

    run = db.get(Run, run_id)
    conversation = db.get(Conversation, run.conversation_id) if run else None
    if not run or run.workflow_id != workflow_id or not conversation or conversation.user_id != mcp_user_id:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "Workflow run not found.")
    output = db.get(Message, run.output_message_id) if run.output_message_id else None
    human_task: HumanTask | None = get_pending_human_task(db, run.id)
    metadata = output.metadata_json if output and isinstance(output.metadata_json, dict) else {}
    structured = {
        "status": run.status,
        "phase": run.phase,
        "answer": output.content if output else "",
        "conversation_id": run.conversation_id,
        "run_id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_version_id": run.workflow_version_id,
        "tool_calls": metadata.get("tool_calls", []),
        "retrieved_chunks": metadata.get("retrieved_chunks", []),
        "human_task_id": human_task.id if human_task else None,
        "error": run.error,
    }
    text = structured["answer"] if run.status == "success" else json.dumps(structured, ensure_ascii=False)
    return build_jsonrpc_result(
        request_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": text,
                }
            ],
            "structuredContent": structured,
            "isError": run.status == "error",
        },
    )
