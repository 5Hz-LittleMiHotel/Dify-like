# Architecture

MVP 采用“配置驱动的固定模板链路”，不是把流程散落写死在业务代码里。

```text
User Input -> Retrieval -> ReAct-like Agent Runner + Tools -> Stream Output -> Logs
```

当前项目先实现一个默认 workflow spec：

```json
{
  "nodes": [
    { "id": "start", "type": "start" },
    { "id": "retrieval", "type": "retrieval", "enabled": true },
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

后续可以把它升级成：

1. 模板选择：basic chat / rag chat / react with tools / multi-agent
2. JSON/YAML workflow 配置
3. 自然语言生成 workflow spec
4. 可视化 workflow 编排

## Runtime 分层

```text
FastAPI Route
  -> ChatService
    -> WorkflowExecutor
      -> RetrievalNode
      -> AgentRunner
      -> ToolRegistry
      -> RunLogService
```

AgentRunner 当前是一个本地可运行的 ReAct-like mock runner，用于先跑通平台闭环。接入真实 AgentScope 时，只需要替换 `app/runtime/agent_runner.py`，上层 API、日志、前端页面不需要大改。
