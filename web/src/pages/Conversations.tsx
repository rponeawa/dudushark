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

function classifyKey(key: string): "group" | "private" {
  // Group conversation keys are pure numeric (group_id)
  // Private conversation keys are user_id
  // Actually both are numeric, but groups are stored with just group_id
  // and private chats are just user_id. We can't distinguish from the key alone.
  // In the backend, groups use group_id as key, private uses user_id as key.
  // They're both strings of numbers. But we know from context:
  // group conversations can have multiple users speaking, private only the user.
  // For display purposes, we'll look at the conversation messages to detect.
  return /^\d{5,}$/.test(key) ? "group" : "private";
}

export default function Conversations({ instances, activeQQ, setActiveQQ }: Props) {
  const [convoKeys, setConvoKeys] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"all" | "group" | "private">("all");

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

  // Detect conversation type from messages
  const getConvoType = (key: string, msgs: Message[]): "group" | "private" => {
    // Check if key matches any known pattern - for now use numeric length
    // Group IDs are usually longer (6+ digits)
    if (key.length >= 6 && /^\d+$/.test(key)) return "group";
    return "private";
  };

  // Separate keys into group and private
  const groupKeys = convoKeys.filter((k) => k.length >= 6 && /^\d+$/.test(k));
  const privateKeys = convoKeys.filter((k) => !(k.length >= 6 && /^\d+$/.test(k)));

  const displayedKeys = tab === "group" ? groupKeys
    : tab === "private" ? privateKeys
    : convoKeys;

  if (instances.length === 0) {
    return <div className="empty-state">请先在「实例」页面创建并启动一个实例</div>;
  }

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
          <button className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>全部</button>
          <button className={tab === "group" ? "active" : ""} onClick={() => setTab("group")}>群聊</button>
          <button className={tab === "private" ? "active" : ""} onClick={() => setTab("private")}>私聊</button>
        </div>

        <div className="convo-list" style={{ maxHeight: "55vh", overflowY: "auto" }}>
          {displayedKeys.map((key) => {
            const ctype = classifyKey(key);
            return (
              <div
                key={key}
                className={`convo-item ${selected === key ? "active" : ""}`}
                onClick={() => loadConvo(key)}
              >
                <span className={`convo-tag ${ctype}`}>
                  {ctype === "group" ? "群" : "私"}
                </span>
                <span className="key-text">{key}</span>
              </div>
            );
          })}
        </div>
        {displayedKeys.length === 0 && (
          <div className="text-dim mt-sm">暂无对话</div>
        )}
      </div>

      {/* Chat view */}
      <div className="panel">
        <div className="panel-header">
          <h2>
            {selected ? (
              <>
                {classifyKey(selected) === "group" ? "群聊 " : "私聊 "}
                <span className="text-mono">{selected}</span>
              </>
            ) : (
              "选择对话"
            )}
          </h2>
          {selected && (
            <button className="btn-danger btn-sm" onClick={handleClear}>清除</button>
          )}
        </div>
        {loading ? (
          <div className="empty-state">加载中...</div>
        ) : messages.length === 0 ? (
          <div className="empty-state">{selected ? "对话为空" : "从左侧选择对话查看"}</div>
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
