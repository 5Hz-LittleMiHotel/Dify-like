from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


DEFAULT_WORKFLOW_SPEC = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "retrieval", "type": "retrieval", "enabled": True},
        {"id": "agent", "type": "react_agent"},
        {"id": "end", "type": "end"},
    ],
    "edges": [["start", "retrieval"], ["retrieval", "agent"], ["agent", "end"]],
}


class AppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = "你是一个专业、耐心的电商客服智能体。"
    model_provider: str = "mock"
    model_name: str = "mock-react"
    temperature: int = 70
    top_p: int = 100
    max_tokens: int = 1024
    workflow_spec: dict[str, Any] = Field(default_factory=lambda: DEFAULT_WORKFLOW_SPEC.copy())


class AppUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    system_prompt: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    temperature: int | None = None
    top_p: int | None = None
    max_tokens: int | None = None
    workflow_spec: dict[str, Any] | None = None


class AppOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    system_prompt: str
    model_provider: str
    model_name: str
    temperature: int
    top_p: int
    max_tokens: int
    workflow_spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolOut(BaseModel):
    name: str
    label: str
    description: str


class AppToolUpdate(BaseModel):
    tool_names: list[str]


class AppToolOut(BaseModel):
    tool_name: str
    enabled: bool


class ChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    conversation_id: str
    run_id: str
    answer: str
    tool_calls: list[dict[str, Any]]
    retrieved_chunks: list[dict[str, Any]]


class DocumentOut(BaseModel):
    id: str
    app_id: str
    filename: str
    status: str
    error: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    app_id: str
    conversation_id: str
    status: str
    latency_ms: int
    error: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunStepOut(BaseModel):
    id: str
    run_id: str
    type: str
    name: str
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    latency_ms: int
    error: str
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}
