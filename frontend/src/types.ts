export type AppItem = {
  id: string;
  name: string;
  description: string;
  status: string;
  system_prompt: string;
  model_provider: string;
  model_name: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  workflow_spec: Record<string, unknown>;
};

export type ToolItem = {
  name: string;
  label: string;
  description: string;
};

export type AppTool = {
  tool_name: string;
  enabled: boolean;
};

export type RunItem = {
  id: string;
  status: string;
  latency_ms: number;
  error: string;
  created_at: string;
};

export type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};
