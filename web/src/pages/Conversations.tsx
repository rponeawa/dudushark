import { useState, useEffect } from "react";
import { listConversations, getConversation, clearConversation, InstanceInfo } from "../api";

interface Props { instances: InstanceInfo[]; activeQQ: string; setActiveQQ: (qq: string) => void; }
interface Message { role: string; content: string; }
interface ConvoItem { key: string; type: "group" | "private"; }

export default function Conversations({ instances, activeQQ, setActiveQQ }: Props) {
  const [convoItems, setConvoItems] = useState<ConvoItem[]>([]);
  const [selected, setSelected] = useState("");
  const [selectedType, setSelectedType] = useState<"group" | "private">("private");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"all" | "group" | "private">("all");

  useEffect(() => {
    if (!activeQQ) return;
    listConversations(activeQQ)
      .then((d) => setConvoItems(d.conversations))
      .catch(() => setConvoItems([]));
    setSelected(""); setMessages([]);
  }, [activeQQ]);

  const loadConvo = async (key: string, ctype: "group" | "private") => {
    setSelected(key); setSelectedType(ctype); setLoading(true);
    try { setMessages((await getConversation(activeQQ, key)).messages); } catch { setMessages([]); }
    setLoading(false);
  };

  const handleClear = async () => {
    if (!selected) return;
    await clearConversation(activeQQ, selected);
    setMessages([]);
    setConvoItems((prev) => prev.filter((c) => c.key !== selected));
    setSelected("");
  };

  const groupItems = convoItems.filter((c) => c.type === "group");
  const privateItems = convoItems.filter((c) => c.type === "private");
  const displayed = tab === "group" ? groupItems : tab === "private" ? privateItems : convoItems;

  if (instances.length === 0) {
    return <div className="empty-state">请先在「实例」页面创建并启动一个实例</div>;
  }

  return (
    <div className="two-col">
      <div className="panel two-col-side">
        <div className="form-group">
          <label>实例</label>
          <select value={activeQQ} onChange={(e) => setActiveQQ(e.target.value)}>
            {instances.map((i) => (<option key={i.qq} value={i.qq}>{i.qq}</option>))}
          </select>
        </div>
        <button className="btn-ghost btn-sm" style={{ marginBottom: 12, width: "100%" }}
          onClick={() => { listConversations(activeQQ).then((d) => setConvoItems(d.conversations)).catch(() => setConvoItems([])); }}>
          刷新列表
        </button>

        <div className="tabs">
          <button className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>全部</button>
          <button className={tab === "group" ? "active" : ""} onClick={() => setTab("group")}>群聊</button>
          <button className={tab === "private" ? "active" : ""} onClick={() => setTab("private")}>私聊</button>
        </div>

        <div className="convo-list" style={{ maxHeight: "55vh", overflowY: "auto" }}>
          {displayed.map((c) => (
            <div
              key={c.key}
              className={`convo-item ${selected === c.key ? "active" : ""}`}
              onClick={() => loadConvo(c.key, c.type)}
            >
              <span className={`convo-tag ${c.type}`}>{c.type === "group" ? "群" : "私"}</span>
              <span className="key-text">{c.key}</span>
            </div>
          ))}
        </div>
        {displayed.length === 0 && (<div className="text-dim mt-sm">暂无对话记录</div>)}
      </div>

      <div className="panel">
        <div className="panel-header">
          <h2>
            {selected ? (<>{selectedType === "group" ? "群聊 " : "私聊 "}<span className="text-mono">{selected}</span></>) : "选择对话"}
          </h2>
          {selected && (
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn-ghost btn-sm" onClick={() => loadConvo(selected, selectedType)}>刷新</button>
              <button className="btn-danger btn-sm" onClick={handleClear}>清除</button>
            </div>
          )}
        </div>
        {loading ? (<div className="empty-state">加载中...</div>)
        : messages.length === 0 ? (<div className="empty-state">{selected ? "对话为空" : "从左侧选择对话查看"}</div>)
        : (<div className="chat-log">
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg ${m.role === "user" ? "user" : "bot"}`}>
                <div className="msg-meta">{m.role === "user" ? "用户" : "嘟嘟鲨鱼"}</div>
                <div className="msg-text">{m.content}</div>
              </div>
            ))}
          </div>)}
      </div>
    </div>
  );
}
