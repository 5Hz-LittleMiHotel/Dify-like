# Architecture

MVP 采用“配置驱动的固定模板链路”，不是把流程散落写死在业务代码里。

```text
User Input -> WorkflowExecutor -> Node Executors -> AgentScope Adapter -> Stream Output -> Logs
```

当前项目的默认 workflow spec：

```json
{
  "nodes": [
    { "id": "start", "type": "start" },
    { "id": "retrieval", "type": "retrieval", "enabled": true, "top_k": 3 },
    { "id": "agent", "type": "react_agent" },
    { "id": "end", "type": "end" }
  ],
  "edges": [
    ["start", "retrieval"],
    ["retrieval", "agent"],
    ["agent", "end"]
  ]
}
```

## Runtime 分层

```text
FastAPI Route
  -> ChatService
    -> WorkflowExecutor
      -> Node Executors
      -> AgentScope Adapter
      -> Tool Registry
      -> Run Log Service
```

`WorkflowExecutor` 负责解释 `workflow_spec` 并按节点顺序执行。  
`AgentScopeAdapter` 负责把 agent 节点映射到 AgentScope 的 `ReActAgent`、`Toolkit`、`Msg`、`stream_printing_messages` 等原语。

节点上的 `model` 配置是可选的，默认继承应用级别的模型配置；如果节点自己显式写了 `model`，它会覆盖应用默认值。

## AgentScope 嵌入点

当前项目里，AgentScope 最适合放在这三处：

- [backend/app/runtime/agent_adapters.py](../backend/app/runtime/agent_adapters.py)
- [backend/app/runtime/workflow_executor.py](../backend/app/runtime/workflow_executor.py)
- [backend/app/services/chat_service.py](../backend/app/services/chat_service.py)

原则是：

- `workflow_spec` 是数据
- `WorkflowExecutor` 是解释器
- `AgentScopeAdapter` 是执行引擎适配层
- `chat_service` 只负责编排和持久化
