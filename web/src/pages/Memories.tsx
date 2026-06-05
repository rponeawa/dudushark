import { useState, useEffect } from "react";
import {
  listMemoryUsers,
  listUserMemories,
  searchMemories,
  createMemory,
  deleteMemory,
  clearMemories,
  InstanceInfo,
  MemoryItem,
  MemorySearchResult,
} from "../api";

interface Props {
  instances: InstanceInfo[];
  activeQQ: string;
  setActiveQQ: (qq: string) => void;
}

type MemoryTab = "personal" | "group" | "diary";

function classifyUsers(users: string[]) {
  const personal: string[] = [];
  const groups: string[] = [];
  let diary: string | null = null;
  for (const u of users) {
    if (u === "__diary__") diary = u;
    else if (u.startsWith("__group__")) groups.push(u);
    else personal.push(u);
  }
  return { personal, groups, diary };
}

function groupIdLabel(key: string) {
  if (key.startsWith("__group__")) return key.slice("__group__".length);
  return key;
}

export default function Memories({ instances, activeQQ, setActiveQQ }: Props) {
  const [users, setUsers] = useState<string[]>([]);
  const [selectedUser, setSelectedUser] = useState("");
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [searchResults, setSearchResults] = useState<MemorySearchResult[]>([]);
  const [searchQ, setSearchQ] = useState("");
  const [viewMode, setViewMode] = useState<"all" | "search">("all");
  const [tab, setTab] = useState<MemoryTab>("personal");

  // Create form
  const [newCat, setNewCat] = useState("日常");
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");

  const classified = classifyUsers(users);

  useEffect(() => {
    if (!activeQQ) return;
    listMemoryUsers(activeQQ)
      .then((d) => setUsers(d.users))
      .catch(() => setUsers([]));
    setSelectedUser("");
    setMemories([]);
    setSearchResults([]);
  }, [activeQQ]);

  useEffect(() => {
    // Auto-select first user in current tab
    const list = tab === "personal" ? classified.personal
      : tab === "group" ? classified.groups
      : classified.diary ? [classified.diary] : [];
    if (list.length > 0) {
      setSelectedUser(list[0]);
      loadMemories(list[0]);
    } else {
      setSelectedUser("");
      setMemories([]);
    }
  }, [tab, users]);

  const loadMemories = async (userId: string) => {
    setSelectedUser(userId);
    setViewMode("all");
    try {
      const d = await listUserMemories(activeQQ, userId);
      setMemories(d.memories);
    } catch {
      setMemories([]);
    }
  };

  const handleSearch = async () => {
    if (!searchQ.trim() || !selectedUser) return;
    setViewMode("search");
    try {
      const d = await searchMemories(activeQQ, selectedUser, searchQ);
      setSearchResults(d.results);
    } catch {
      setSearchResults([]);
    }
  };

  const handleCreate = async () => {
    if (!selectedUser || !newTitle.trim() || !newContent.trim()) return;
    await createMemory(activeQQ, selectedUser, newCat, newTitle.trim(), newContent.trim());
    setNewTitle("");
    setNewContent("");
    loadMemories(selectedUser);
  };

  const handleDelete = async (category: string, title: string) => {
    await deleteMemory(activeQQ, selectedUser, category, title);
    loadMemories(selectedUser);
  };

  const parseMemoryItem = (item: MemoryItem) => {
    const text = item.text;
    const titleMatch = text.match(/^# (.+)/m);
    const catMatch = text.match(/类型: (.+)/);
    const dateMatch = text.match(/时间: (.+)/);
    const idMatch = text.match(/ID: (.+)/);
    const contentMatch = text.match(/\n\n(.+)/s);
    return {
      title: titleMatch?.[1] || item.file,
      category: catMatch?.[1] || "",
      date: dateMatch?.[1] || "",
      id: idMatch?.[1] || "",
      content: contentMatch?.[1] || text,
    };
  };

  if (instances.length === 0) {
    return <div className="empty-state">请先在「实例」页面创建并启动一个实例</div>;
  }

  const userList = tab === "personal" ? classified.personal
    : tab === "group" ? classified.groups
    : classified.diary ? [classified.diary] : [];

  return (
    <div className="two-col">
      {/* Sidebar */}
      <div className="panel two-col-side">
        <div className="form-group">
          <label>实例</label>
          <select value={activeQQ} onChange={(e) => setActiveQQ(e.target.value)}>
            {instances.map((i) => (
              <option key={i.qq} value={i.qq}>{i.qq}</option>
            ))}
          </select>
        </div>

        <div className="tabs">
          <button className={tab === "personal" ? "active" : ""} onClick={() => setTab("personal")}>
            个人记忆
          </button>
          <button className={tab === "group" ? "active" : ""} onClick={() => setTab("group")}>
            群聊记忆
          </button>
          <button className={tab === "diary" ? "active" : ""} onClick={() => setTab("diary")}>
            全局记忆
          </button>
        </div>

        <div className="convo-list" style={{ maxHeight: "50vh", overflowY: "auto" }}>
          {userList.map((u) => (
            <div
              key={u}
              className={`convo-item ${selectedUser === u ? "active" : ""}`}
              onClick={() => loadMemories(u)}
            >
              <span className="key-text">
                {tab === "group" ? groupIdLabel(u) : tab === "diary" ? "全局记忆" : u}
              </span>
            </div>
          ))}
        </div>
        {userList.length === 0 && (
          <div className="text-dim mt-sm">暂无数据</div>
        )}
      </div>

      {/* Main */}
      <div>
        {selectedUser ? (
          <>
            <div className="panel">
              <div className="search-bar">
                <input
                  value={searchQ}
                  onChange={(e) => setSearchQ(e.target.value)}
                  placeholder="向量检索..."
                  onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                />
                <button className="btn-primary" onClick={handleSearch}>搜索</button>
                <button className="btn-ghost" onClick={() => { setViewMode("all"); loadMemories(selectedUser); }}>
                  全部
                </button>
              </div>

              {viewMode === "search" && searchResults.length > 0 && (
                <div className="mem-list">
                  {searchResults.map((r) => (
                    <div key={r.id} className="mem-item">
                      <div className="mem-header">
                        <span className="mem-cat">{r.meta.category || "memory"}</span>
                        <span className="mem-title">{r.meta.title || r.id}</span>
                        <span className="mem-date">{r.meta.date || ""}</span>
                        <span style={{ fontSize: "0.7rem", color: "var(--text-dim)" }}>
                          相似度 {r.score.toFixed(3)}
                        </span>
                      </div>
                      <div className="mem-content">{r.text.slice(0, 500)}</div>
                    </div>
                  ))}
                </div>
              )}
              {viewMode === "search" && searchResults.length === 0 && (
                <div className="empty-state">未找到</div>
              )}
            </div>

            {/* Create memory */}
            <div className="panel">
              <div className="panel-header">
                <h2>添加记忆</h2>
              </div>
              <div className="form-row">
                <div className="form-group" style={{ flex: "0 0 120px" }}>
                  <label>分类</label>
                  <select value={newCat} onChange={(e) => setNewCat(e.target.value)}>
                    {["日常", "对话记忆", "重要事件", "情感", "知识", "备注"].map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>标题</label>
                  <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="记忆标题" />
                </div>
              </div>
              <div className="form-group">
                <label>内容</label>
                <textarea
                  value={newContent}
                  onChange={(e) => setNewContent(e.target.value)}
                  placeholder="记忆内容..."
                  rows={3}
                />
              </div>
              <button className="btn-primary" onClick={handleCreate}>添加</button>
            </div>

            {/* All memories */}
            {viewMode === "all" && (
              <div className="panel">
                <div className="panel-header">
                  <h2>{memories.length} 条记忆</h2>
                  <button
                    className="btn-danger btn-sm"
                    onClick={async () => {
                      if (confirm("确定清空全部记忆？")) {
                        await clearMemories(activeQQ, selectedUser);
                        setMemories([]);
                      }
                    }}
                  >
                    清空
                  </button>
                </div>
                {memories.length === 0 ? (
                  <div className="empty-state">暂无记忆</div>
                ) : (
                  <div className="mem-list">
                    {memories.map((m, idx) => {
                      const parsed = parseMemoryItem(m);
                      return (
                        <div key={idx} className="mem-item">
                          <div className="mem-header">
                            <span className="mem-cat">{parsed.category}</span>
                            <span className="mem-title">{parsed.title}</span>
                            <span className="mem-date">{parsed.date}</span>
                          </div>
                          <div className="mem-content">{parsed.content.slice(0, 800)}</div>
                          <div className="mem-actions">
                            <button
                              className="btn-danger btn-sm"
                              onClick={() => handleDelete(parsed.category, parsed.title)}
                            >
                              删除
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="panel">
            <div className="empty-state">从左侧选择查看记忆</div>
          </div>
        )}
      </div>
    </div>
  );
}
