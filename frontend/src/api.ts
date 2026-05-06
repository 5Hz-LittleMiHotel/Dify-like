import type { AppItem, AppTool, RunItem, ToolItem } from "./types";

const API_BASE = "http://localhost:8000/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export const api = {
  listApps: () => request<AppItem[]>("/apps"),
  createApp: () =>
    request<AppItem>("/apps", {
      method: "POST",
      body: JSON.stringify({
        name: "电商客服 Agent",
        description: "用于演示订单查询、FAQ 检索和运行日志。",
        system_prompt: "你是一个专业、耐心、简洁的电商客服智能体。",
      }),
    }),
  updateApp: (appId: string, payload: Partial<AppItem>) =>
    request<AppItem>(`/apps/${appId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listTools: () => request<ToolItem[]>("/tools"),
  listAppTools: (appId: string) => request<AppTool[]>(`/apps/${appId}/tools`),
  updateAppTools: (appId: string, toolNames: string[]) =>
    request<AppTool[]>(`/apps/${appId}/tools`, {
      method: "PUT",
      body: JSON.stringify({ tool_names: toolNames }),
    }),
  listRuns: (appId: string) => request<RunItem[]>(`/apps/${appId}/runs`),
  uploadDocument: async (appId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/apps/${appId}/documents`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  },
};

export async function streamChat(
  appId: string,
  query: string,
  conversationId: string | null,
  onEvent: (event: string, data: Record<string, unknown>) => void,
) {
  const response = await fetch(`${API_BASE}/apps/${appId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, conversation_id: conversationId, stream: true }),
  });
  if (!response.ok || !response.body) {
    throw new Error(await response.text());
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      const eventLine = part.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      onEvent(eventLine.slice(7), JSON.parse(dataLine.slice(6)));
    }
  }
}
