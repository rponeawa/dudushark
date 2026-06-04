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

export default function Memories({ instances, activeQQ, setActiveQQ }: Props) {
  const [users, setUsers] = useState<string[]>([]);
  const [selectedUser, setSelectedUser] = useState("");
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [searchResults, setSearchResults] = useState<MemorySearchResult[]>([]);
  const [searchQ, setSearchQ] = useState("");
  const [viewMode, setViewMode] = useState<"all" | "search">("all");

  // Create form
  const [newCat, setNewCat] = useState("日常");
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");

  useEffect(() => {
    if (!activeQQ) return;
    listMemoryUsers(activeQQ)
      .then((d) => setUsers(d.users))
      .catch(() => setUsers([]));
    setSelectedUser("");
    setMemories([]);
    setSearchResults([]);
  }, [activeQQ]);

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
    return <div className="empty-state">请先在「实例管理」中创建并启动一个实例～</div>;
  }

  return (
    <div className="two-col" style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: "12px", alignItems: "start" }}>
      {/* Sidebar */}
      <div className="panel" style={{ position: "sticky", top: 60 }}>
        <div className="form-group">
          <label>选择实例</label>
          <select value={activeQQ} onChange={(e) => setActiveQQ(e.target.value)}>
            {instances.map((i) => (
              <option key={i.qq} value={i.qq}>{i.qq}</option>
            ))}
          </select>
        </div>

        <div className="form-group">
          <label>用户列表</label>
          <div className="convo-list" style={{ maxHeight: "300px", overflowY: "auto" }}>
            {users.map((u) => (
              <div
                key={u}
                className={`convo-item ${selectedUser === u ? "active" : ""}`}
                onClick={() => loadMemories(u)}
              >
                <span className="key-text">{u}</span>
              </div>
            ))}
          </div>
          {users.length === 0 && <div className="text-dim mt-sm">暂无记忆数据</div>}
        </div>
      </div>

      {/* Main */}
      <div>
        {selectedUser ? (
          <>
            {/* Search */}
            <div className="panel">
              <div className="search-bar">
                <input
                  value={searchQ}
                  onChange={(e) => setSearchQ(e.target.value)}
                  placeholder="向量检索记忆..."
                  onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                />
                <button className="btn-primary" onClick={handleSearch}>搜索</button>
                <button className="btn-ghost" onClick={() => { setViewMode("all"); loadMemories(selectedUser); }}>显示全部</button>
              </div>

              {viewMode === "search" && searchResults.length > 0 && (
                <div className="mem-list">
                  {searchResults.map((r) => (
                    <div key={r.id} className="mem-item">
                      <div className="mem-header">
                        <span className="mem-cat">{r.meta.category || "memory"}</span>
                        <span className="mem-title">{r.meta.title || r.id}</span>
                        <span className="mem-date">{r.meta.date || ""}</span>
                        <span className="text-dim" style={{ fontSize: "0.72rem" }}>相似度: {r.score.toFixed(3)}</span>
                      </div>
                      <div className="mem-content">{r.text.slice(0, 500)}</div>
                    </div>
                  ))}
                </div>
              )}
              {viewMode === "search" && searchResults.length === 0 && (
                <div className="empty-state">未找到相关记忆</div>
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
                  <h2>全部记忆 ({memories.length})</h2>
                  <button
                    className="btn-danger btn-sm"
                    onClick={async () => {
                      if (confirm("确定清空此用户所有记忆？")) {
                        await clearMemories(activeQQ, selectedUser);
                        setMemories([]);
                      }
                    }}
                  >
                    清空全部
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
            <div className="empty-state">请从左侧选择一个用户来查看记忆</div>
          </div>
        )}
      </div>
    </div>
  );
}
