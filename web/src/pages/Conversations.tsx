import { useState, useEffect } from "react";
import { listConversations, getConversation, clearConversation, InstanceInfo } from "../api";

interface Props {
  instances: InstanceInfo[];
  activeQQ: string;
  setActiveQQ: (qq: string) => void;
}

interface Message {
  role: string;
  content: string;
}

export default function Conversations({ instances, activeQQ, setActiveQQ }: Props) {
  const [convoKeys, setConvoKeys] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!activeQQ) return;
    listConversations(activeQQ)
      .then((d) => setConvoKeys(d.conversations))
      .catch(() => setConvoKeys([]));
    setSelected("");
    setMessages([]);
  }, [activeQQ]);

  const loadConvo = async (key: string) => {
    setSelected(key);
    setLoading(true);
    try {
      const d = await getConversation(activeQQ, key);
      setMessages(d.messages);
    } catch {
      setMessages([]);
    }
    setLoading(false);
  };

  const handleClear = async () => {
    if (!selected) return;
    await clearConversation(activeQQ, selected);
    setMessages([]);
    setConvoKeys((prev) => prev.filter((k) => k !== selected));
    setSelected("");
  };

  if (instances.length === 0) {
    return <div className="empty-state">请先在「实例管理」中创建并启动一个实例～</div>;
  }

  return (
    <div className="two-col" style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: "12px", alignItems: "start" }}>
      {/* Instance selector + convo list */}
      <div className="panel" style={{ position: "sticky", top: 60 }}>
        <div className="form-group">
          <label>选择实例</label>
          <select value={activeQQ} onChange={(e) => setActiveQQ(e.target.value)}>
            {instances.map((i) => (
              <option key={i.qq} value={i.qq}>{i.qq} <span className={`status-dot ${i.connected ? "online" : "offline"}`} /></option>
            ))}
          </select>
        </div>

        <div className="convo-list" style={{ maxHeight: "60vh", overflowY: "auto" }}>
          {convoKeys.map((key) => (
            <div
              key={key}
              className={`convo-item ${selected === key ? "active" : ""}`}
              onClick={() => loadConvo(key)}
            >
              <span className="key-text">{key}</span>
            </div>
          ))}
        </div>
        {convoKeys.length === 0 && (
          <div className="text-dim mt-sm">暂无对话记录</div>
        )}
      </div>

      {/* Chat view */}
      <div className="panel">
        <div className="panel-header">
          <h2>{selected || "选择对话"}</h2>
          {selected && (
            <button className="btn-danger btn-sm" onClick={handleClear}>清除对话</button>
          )}
        </div>
        {loading ? (
          <div className="empty-state">加载中...</div>
        ) : messages.length === 0 ? (
          <div className="empty-state">{selected ? "对话为空" : "请从左侧选择对话"}</div>
        ) : (
          <div className="chat-log">
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg ${m.role === "user" ? "user" : "bot"}`}>
                <div className="msg-meta">{m.role === "user" ? "用户" : "嘟嘟鲨鱼"}</div>
                <div className="msg-text">{m.content}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
