# System Architecture and Roadmap

这份文档用于回答三个问题：

1. 现在整个系统架构是什么？
2. 当前开发处于什么位置？
3. 后续还需要怎么改进？

## 1. 项目定位

本项目不是完整复刻 Dify，而是做一个基于 AgentScope 的 Dify-like 智能体应用平台 Demo。

更准确地说：

- Dify 提供产品形态参考：应用管理、Prompt 配置、工具、知识库、调试、日志、API 调用。
- AgentScope 作为底层 agent runtime：负责 agent 执行、工具调用、memory、多 agent、streaming 等能力。
- 本项目的平台层负责把用户配置转成可执行的 workflow，再交给 runtime 执行。

一句话：

```text
前台和平台层学习 Dify，后端 agent 执行层使用 AgentScope。
```

## 2. 当前系统分层

```text
Frontend Console
  -> FastAPI Routes
    -> Services
      -> WorkflowExecutor
        -> Node Executors
          -> Retrieval
          -> Agent Adapter
          -> Tool Registry
        -> Run Logs
```

更具体：

```text
React Playground
  -> POST /api/apps/{app_id}/chat
    -> chat_service
      -> WorkflowExecutor
        -> start node
        -> retrieval node
        -> agent node
          -> MockAgentAdapter / AgentScopeAdapter
        -> end node
      -> messages / runs / run_steps
```

## 3. 核心概念

### App

一个 App 对应一个智能体应用配置。

它包含：

- 名称
- 描述
- system prompt
- 模型参数
- workflow_spec
- 启用的工具
- 上传的知识库文档

### workflow_spec

`workflow_spec` 是平台最核心的数据结构。它描述一个应用的执行流程。

示例：

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

含义：

- `nodes` 表示有哪些步骤。
- `edges` 表示步骤之间如何连接。
- `WorkflowExecutor` 负责解释这份配置。

### WorkflowExecutor

`WorkflowExecutor` 是 workflow 的解释器。

它负责：

- 读取 `workflow_spec`
- 根据 `edges` 排出执行顺序
- 执行 start / retrieval / agent / tool / end 等节点
- 收集最终回答、工具调用、检索片段
- 写入 run_steps 日志
- 向上层输出 SSE event

### Agent Adapter

Agent Adapter 是 agent 节点的执行适配层。

当前有两个方向：

- `MockAgentAdapter`: 本地可跑，不依赖真实模型 API。
- `AgentScopeAdapter`: 准备接入真实 AgentScope。

设计意图：

```text
WorkflowExecutor 不直接依赖 AgentScope。
WorkflowExecutor 只调用 adapter。
adapter 再决定使用 mock 还是 AgentScope。
```

这样可以让平台逻辑和 agent runtime 解耦。

## 4. 当前已完成内容

### 平台基础

已完成：

- 项目目录结构
- FastAPI 后端
- React 前端
- Docker Compose
- PostgreSQL / Redis 容器配置
- `.env` 配置模板

### 应用管理

已完成：

- 创建应用
- 查看应用
- 更新应用
- 删除应用
- 保存应用级模型参数
- 保存 `workflow_spec`

### 工具管理

已完成：

- 内置工具列表
- 应用级工具启用/禁用
- mock 订单查询工具
- 当前时间工具
- mock 天气工具
- 简单计算器工具

### 知识库

已完成简化版：

- 上传 `.txt` / `.md`
- 文档切 chunk
- 本地文件保存
- 数据库存储 chunk
- 简单关键词检索

尚未完成：

- embedding
- pgvector 检索
- 多知识库
- 文档解析
- 检索参数可视化配置

### 聊天调试

已完成：

- Playground 页面
- SSE 流式输出
- 用户消息
- Agent 回复
- 当前 trace 展示
- 最近 runs 展示

### 运行日志

已完成：

- runs 表
- run_steps 表
- retrieval step
- tool_call step
- agent step
- start / end step
- error step

## 5. 当前开发阶段

当前处于：

```text
阶段 1：平台骨架 + Workflow Runtime 抽象
```

这说明项目已经不是简单的聊天 demo，而是开始向 Dify-like 平台演进。

当前最重要的成果是：

```text
原来直接写死的流程，已经被抽象为 workflow_spec + WorkflowExecutor + Agent Adapter。
```

现在还不是完整低代码平台，但已经有低代码平台的核心雏形。

## 6. 后续开发阶段

### 阶段 2：workflow 配置可编辑

目标：

- 前端展示 workflow_spec
- 支持编辑节点参数
- 支持编辑 edges
- 支持保存 workflow 配置
- 支持查看每个节点的运行日志

建议先做 JSON 编辑器，不急着做拖拽画布。

### 阶段 3：接入真实 AgentScope

目标：

- 安装并验证 AgentScope
- 配置模型 API
- 使用 AgentScope ReActAgent 执行 agent 节点
- 将平台工具注册到 AgentScope Toolkit
- 将检索结果注入 system prompt 或作为工具暴露给 agent
- 保留 mock adapter 作为 fallback

### 阶段 4：真实 RAG

目标：

- 加 embedding
- 启用 pgvector
- 存储 chunk embedding
- 支持相似度检索
- 支持 top_k / score threshold
- 在 trace 中显示检索分数

### 阶段 5：更完整的工作流能力

目标：

- 条件边
- router 节点
- tool 节点
- 多 agent 节点
- supervisor / worker
- 子 workflow
- workflow template

### 阶段 6：低代码 UI

目标：

- 节点面板
- 节点配置表单
- 连线配置
- 调试每个节点
- 运行 trace 时间线
- 后续再考虑拖拽画布

## 7. 后续是否需要大改架构

目前不需要推倒重来。

后续会有扩展，但核心方向已经对：

```text
App Config
  -> workflow_spec
    -> WorkflowExecutor
      -> AgentScopeAdapter
      -> logs
```

后面主要是把这套结构补完整：

- workflow 从线性走向图结构
- agent 从 mock 走向 AgentScope
- RAG 从关键词检索走向向量检索
- 前端从配置面板走向低代码 workflow 编辑器

## 8. 当前架构的边界

当前还比较简化：

- workflow 只支持线性执行
- edges 还没有条件
- tool 节点还很基础
- AgentScopeAdapter 还未真实跑通模型 API
- memory 还没有完整接入
- 前端还没有 workflow 编辑器
- run detail 页面还不完整

这些不是方向错误，而是 MVP 阶段的正常边界。

## 9. 一句话总结

当前项目已经完成了从“聊天 demo”到“平台 runtime 雏形”的关键转变。

下一步重点不是再加业务规则，而是继续完善：

```text
workflow 配置能力 + AgentScopeAdapter + 节点级日志 + 前端低代码配置
```
