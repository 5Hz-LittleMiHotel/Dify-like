## OpenTelemetry 是什么

那先不谈“父 Span”，从最基础讲。

**OpenTelemetry 是什么**

OpenTelemetry，简称 OTel，是一套**记录和传输程序运行过程的行业标准**。

它本身不是展示页面，也不是数据库。它规定了：

- 一次调用怎么记录
- 调用之间的父子关系怎么表示
- 耗时、输入、输出、错误怎么保存
- 这些记录怎么发送给 Jaeger 等系统

**结合你的项目理解**

一次用户提问可能经过：

```
执行 Agent
-> 调用大模型
-> 调用 calculator
-> 再次调用大模型
-> 返回答案
```

OpenTelemetry 会将它记录为：

```
Trace：本次完整调用链
├── Span：ReActAgent 执行
├── Span：第一次调用大模型
├── Span：调用 calculator
└── Span：第二次调用大模型
```

两个核心概念：

- `Trace`：一次完整运行。
- `Span`：这次运行中的一个步骤。

**AgentScope 和它的关系**

AgentScope 的“原生 trace”底层就是 OpenTelemetry。

AgentScope 已经在内部做好了埋点：

```
ReActAgent
LLM
Formatter
Toolkit
```

当这些组件执行时，AgentScope自动生成 Span。你不需要自己监控 calculator 的开始时间和结束时间。

**Jaeger 是什么**

OpenTelemetry 负责生成和发送记录，Jaeger 负责接收、保存和展示：

```
AgentScope
-> 生成 OpenTelemetry Span
-> 使用 OTLP 协议发送
-> Jaeger 保存并展示
```

可以简单理解为：

| 组件          | 职责                           |
| ------------- | ------------------------------ |
| AgentScope    | 自动记录 Agent、模型和工具调用 |
| OpenTelemetry | 定义记录格式和传输方式         |
| OTLP          | 发送 trace 的协议              |
| Jaeger        | 查看和检索 trace 的后台        |

**它与前端实时输出的区别**

```
AgentScope Msg -> SSE -> 聊天页面实时展示
```

这是实时产品功能。

```
AgentScope Trace -> OpenTelemetry -> Jaeger
```

这是后台运行记录，通常在调用完成后查看。

因此第一步可以先简单处理：

1. 启动 Jaeger。
2. 配置 AgentScope 将 trace 发给 Jaeger。
3. 在 Jaeger 中查看 ReActAgent、LLM 和工具 Span。
4. 暂时不增加我之前说的项目父 Span。

等原生 trace 真正跑通后，再决定是否需要用项目 `run_id` 关联 Jaeger 记录。这样更符合你当前的理解和开发阶段。

## 什么是父子关系

“父子关系”表示：**一个操作是在另一个操作执行过程中被发起的。**

例如用户问：

```
计算 123 × 456
```

执行过程：

```
ReActAgent 开始运行
-> ReActAgent 调用大模型
-> 大模型决定调用 calculator
-> ReActAgent 执行 calculator
-> ReActAgent 再次调用大模型生成答案
```

记录成 Trace：

```
ReActAgent Span                 父
├── 第一次 LLM Span             子
├── calculator Span             子
└── 第二次 LLM Span             子
```

这里：

- `ReActAgent` 是父操作，因为它发起并包含后面的操作。
- LLM 和 calculator 是子操作，因为它们发生在 ReActAgent 执行期间。
- 两次 LLM 调用和 calculator 互为同级，不是相互包含。

如果 calculator 内部又访问远程服务：

```
ReActAgent Span
└── calculator Span
    └── HTTP 请求 Span
```

那么 HTTP 请求是 calculator 的子操作，也是 ReActAgent 的孙级操作。

父子关系主要帮助你回答：

- 这次工具调用由哪个 Agent 发起？
- 一次 Agent 执行调用了几次模型？
- 总耗时主要花在哪里？
- 某个错误是模型调用还是工具调用导致的？
- 多个并发请求分别属于哪次运行？

它表达的是**调用链上的包含关系**，不是代码类的继承关系，也不是数据库表关系。