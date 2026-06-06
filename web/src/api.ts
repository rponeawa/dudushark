const BASE = "/api";

let _token: string | null = localStorage.getItem("dudushark_token");

export function getToken() { return _token; }

export function setToken(t: string | null) {
  _token = t;
  if (t) localStorage.setItem("dudushark_token", t);
  else localStorage.removeItem("dudushark_token");
}

async function req<T>(url: string, opts?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (_token) headers["Authorization"] = `Bearer ${_token}`;
  const res = await fetch(BASE + url, { headers, ...opts });
  if (res.status === 401) {
    setToken(null);
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export const login = (password: string) =>
  req<{ token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ password }) });

export interface InstanceInfo {
  qq: string;
  connected: boolean;
  napcat_running: boolean;
}

export interface LLMConfig {
  base_url: string;
  model: string;
  api_key: string;
}

export interface BotConfig {
  qq: string;
  llm: LLMConfig;
  context_max_tokens: number;
  memory_retrieval_count: number;
  web_search_enabled: boolean;
  reply_split_enabled: boolean;
  reply_split_max: number;
  group_reply_ratio: number;
  private_merge_delay: number;
  group_merge_delay: number;
  napcat_webui_port: number;
  onebot_ws_port: number;
  admins: Array<{ qq: string; role: string }>;
  admins_description: string;
  family_memory: string;
  family_note: string;
}

export interface MemoryItem {
  file: string;
  text: string;
}

export interface MemorySearchResult {
  id: string;
  text: string;
  score: number;
  meta: Record<string, string>;
}

export interface ChatMessage {
  role: string;
  content: string;
}

// Status
export interface InstanceStatus {
  qq: string;
  connected: boolean;
  napcat_running: boolean;
  conversation_count: number;
  memory_users: number;
}
export interface SystemStatus {
  uptime: number;
  python_version: string;
  platform: string;
  memory_mb: number;
  data_dir: string;
  llm_ok: boolean;
  instances: InstanceStatus[];
  total_conversations: number;
  total_memories: number;
  recent_events: Array<{ type: string; [key: string]: unknown; _ts: number }>;
}
export interface MoodState {
  sleep_state: string;
  hourly_mood: number;
  energy: number;
}

export interface InstanceDetailStatus {
  qq: string;
  connected: boolean;
  napcat_running: boolean;
  napcat_webui_port: number;
  onebot_ws_port: number;
  conversation_count: number;
  memory_users: string[];
  memory_stats: Record<string, number>;
  total_memories: number;
  mood?: MoodState;
}
export const getSystemStatus = () => req<SystemStatus>("/status");
export const getInstanceStatus = (qq: string) => req<InstanceDetailStatus>(`/instances/${qq}/status`);

// Instances
export const listInstances = () => req<{ instances: InstanceInfo[]; current: string | null }>("/instances");
export const createInstance = (qq: string, napcatPath?: string) =>
  req(`/instances/create?qq=${qq}&napcat_path=${encodeURIComponent(napcatPath || "")}`, { method: "POST" });
export const startInstance = (qq: string) => req(`/instances/${qq}/start`, { method: "POST" });
export const stopInstance = (qq: string) => req(`/instances/${qq}/stop`, { method: "POST" });
export const getQrCode = (qq: string) => req<{ qrcode: string | null }>(`/instances/${qq}/qrcode`);

// Config
export const getConfig = (qq: string) => req<BotConfig>(`/instances/${qq}/config`);
export const updateConfig = (qq: string, data: Partial<BotConfig>) =>
  req(`/instances/${qq}/config`, { method: "PUT", body: JSON.stringify(data) });

// Conversations
export const listConversations = (qq: string) =>
  req<{ conversations: string[] }>(`/instances/${qq}/conversations`);
export const getConversation = (qq: string, key: string) =>
  req<{ key: string; messages: ChatMessage[] }>(`/instances/${encodeURIComponent(qq)}/conversations/${encodeURIComponent(key)}`);
export const clearConversation = (qq: string, key: string) =>
  req(`/instances/${encodeURIComponent(qq)}/conversations/${encodeURIComponent(key)}`, { method: "DELETE" });

// Memories
export const listMemoryUsers = (qq: string) => req<{ users: string[] }>(`/instances/${qq}/memories/users`);
export const listUserMemories = (qq: string, userId: string) =>
  req<{ user_id: string; memories: MemoryItem[] }>(`/instances/${encodeURIComponent(qq)}/memories/${userId}`);
export const searchMemories = (qq: string, userId: string, q: string) =>
  req<{ user_id: string; results: MemorySearchResult[] }>(`/instances/${encodeURIComponent(qq)}/memories/${userId}/search?q=${encodeURIComponent(q)}`);
export const createMemory = (qq: string, userId: string, category: string, title: string, content: string) =>
  req(`/instances/${encodeURIComponent(qq)}/memories/${userId}`, {
    method: "POST",
    body: JSON.stringify({ category, title, content }),
  });
export const deleteMemory = (qq: string, userId: string, category: string, title: string) =>
  req(`/instances/${encodeURIComponent(qq)}/memories/${userId}/${category}/${encodeURIComponent(title)}`, { method: "DELETE" });
export const clearMemories = (qq: string, userId: string) =>
  req(`/instances/${encodeURIComponent(qq)}/memories/${userId}`, { method: "DELETE" });
