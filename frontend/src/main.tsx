import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Bot, Database, FileUp, History, MessageSquare, Play, Save, Wrench } from "lucide-react";
import { api, streamChat } from "./api";
import type { AppItem, AppTool, ChatMessage, RunItem, ToolItem } from "./types";
import "./styles.css";

function App() {
  const [apps, setApps] = useState<AppItem[]>([]);
  const [selectedApp, setSelectedApp] = useState<AppItem | null>(null);
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [appTools, setAppTools] = useState<AppTool[]>([]);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("我的订单 10086 到哪了？");
  const [trace, setTrace] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);

  const enabledToolNames = useMemo(
    () => appTools.filter((item) => item.enabled).map((item) => item.tool_name),
    [appTools],
  );

  async function refresh() {
    const [appList, toolList] = await Promise.all([api.listApps(), api.listTools()]);
    setApps(appList);
    setTools(toolList);
    const app = selectedApp ?? appList[0] ?? null;
    if (app) {
      setSelectedApp(app);
      const [appToolList, runList] = await Promise.all([api.listAppTools(app.id), api.listRuns(app.id)]);
      setAppTools(appToolList);
      setRuns(runList);
    }
  }

  useEffect(() => {
    refresh().catch(console.error);
  }, []);

  async function createDemoApp() {
    const app = await api.createApp();
    setSelectedApp(app);
    await refresh();
  }

  async function saveConfig() {
    if (!selectedApp) return;
    const updated = await api.updateApp(selectedApp.id, selectedApp);
    setSelectedApp(updated);
    await api.updateAppTools(selectedApp.id, enabledToolNames);
    await refresh();
  }

  async function toggleTool(toolName: string) {
    if (!selectedApp) return;
    const next = appTools.some((item) => item.tool_name === toolName && item.enabled)
      ? enabledToolNames.filter((name) => name !== toolName)
      : [...enabledToolNames, toolName];
    const updated = await api.updateAppTools(selectedApp.id, next);
    setAppTools(updated);
  }

  async function sendMessage() {
    if (!selectedApp || !input.trim()) return;
    const query = input.trim();
    setInput("");
    setBusy(true);
    setTrace([]);
    setMessages((items) => [...items, { role: "user", content: query }, { role: "assistant", content: "" }]);

    try {
      await streamChat(selectedApp.id, query, conversationId, (event, data) => {
        if (event === "run_started") {
          setConversationId(String(data.conversation_id));
        }
        if (event === "message_delta") {
          setMessages((items) => {
            const next = [...items];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, content: last.content + String(data.content ?? "") };
            return next;
          });
        }
        if (event === "retrieval" || event === "tool_call" || event === "final") {
          setTrace((items) => [...items, { event, ...data }]);
        }
      });
      if (selectedApp) {
        setRuns(await api.listRuns(selectedApp.id));
      }
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File | null) {
    if (!selectedApp || !file) return;
    await api.uploadDocument(selectedApp.id, file);
    setTrace((items) => [...items, { event: "document_uploaded", filename: file.name }]);
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Bot size={22} />
          <div>
            <h1>Dify-like</h1>
            <p>AgentScope MVP Demo</p>
          </div>
        </div>
        <button className="primary" onClick={createDemoApp}>
          <Play size={16} /> 创建电商客服 Agent
        </button>
        <div className="app-list">
          {apps.map((app) => (
            <button
              key={app.id}
              className={selectedApp?.id === app.id ? "app-item active" : "app-item"}
              onClick={async () => {
                setSelectedApp(app);
                setAppTools(await api.listAppTools(app.id));
                setRuns(await api.listRuns(app.id));
              }}
            >
              <strong>{app.name}</strong>
              <span>{app.status}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="config">
        <header>
          <Wrench size={18} />
          <h2>Agent 配置</h2>
        </header>
        {selectedApp ? (
          <>
            <label>
              名称
              <input
                value={selectedApp.name}
                onChange={(event) => setSelectedApp({ ...selectedApp, name: event.target.value })}
              />
            </label>
            <label>
              描述
              <textarea
                rows={3}
                value={selectedApp.description}
                onChange={(event) => setSelectedApp({ ...selectedApp, description: event.target.value })}
              />
            </label>
            <label>
              System Prompt
              <textarea
                rows={8}
                value={selectedApp.system_prompt}
                onChange={(event) => setSelectedApp({ ...selectedApp, system_prompt: event.target.value })}
              />
            </label>
            <div className="grid-two">
              <label>
                模型
                <input
                  value={selectedApp.model_name}
                  onChange={(event) => setSelectedApp({ ...selectedApp, model_name: event.target.value })}
                />
              </label>
              <label>
                温度
                <input
                  type="number"
                  value={selectedApp.temperature}
                  onChange={(event) => setSelectedApp({ ...selectedApp, temperature: Number(event.target.value) })}
                />
              </label>
            </div>
            <button className="secondary" onClick={saveConfig}>
              <Save size={16} /> 保存配置
            </button>

            <section className="panel">
              <div className="panel-title">
                <Database size={16} /> 知识库
              </div>
              <label className="upload">
                <FileUp size={16} />
                上传 .txt / .md
                <input type="file" accept=".txt,.md" onChange={(event) => upload(event.target.files?.[0] ?? null)} />
              </label>
            </section>

            <section className="panel">
              <div className="panel-title">
                <Wrench size={16} /> 工具
              </div>
              {tools.map((tool) => (
                <label className="check" key={tool.name}>
                  <input
                    type="checkbox"
                    checked={enabledToolNames.includes(tool.name)}
                    onChange={() => toggleTool(tool.name)}
                  />
                  <span>
                    <strong>{tool.label}</strong>
                    <small>{tool.description}</small>
                  </span>
                </label>
              ))}
            </section>
          </>
        ) : (
          <p className="empty">先创建一个 Agent 应用。</p>
        )}
      </section>

      <section className="chat">
        <header>
          <MessageSquare size={18} />
          <h2>Playground</h2>
        </header>
        <div className="messages">
          {messages.length === 0 ? (
            <div className="empty">试试订单查询和退货政策问答。</div>
          ) : (
            messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                {message.content}
              </div>
            ))
          )}
        </div>
        <div className="composer">
          <input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter") sendMessage();
          }} />
          <button className="primary" disabled={busy || !selectedApp} onClick={sendMessage}>
            <Play size={16} /> 发送
          </button>
        </div>
      </section>

      <aside className="trace">
        <header>
          <History size={18} />
          <h2>Logs</h2>
        </header>
        <section className="panel">
          <div className="panel-title">当前 Trace</div>
          <pre>{trace.length ? JSON.stringify(trace, null, 2) : "暂无 trace"}</pre>
        </section>
        <section className="panel">
          <div className="panel-title">最近 Runs</div>
          {runs.map((run) => (
            <div className="run" key={run.id}>
              <strong>{run.status}</strong>
              <span>{run.latency_ms} ms</span>
            </div>
          ))}
        </section>
      </aside>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
