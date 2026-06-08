import { useState, useEffect } from "react";
import { getQzonePosts, qzoneManualPost, QzonePost } from "../api";

interface Props { activeQQ: string; }

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function QzonePage({ activeQQ }: Props) {
  const [posts, setPosts] = useState<QzonePost[]>([]);
  const [loading, setLoading] = useState(false);
  const [content, setContent] = useState("");

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { const d = await getQzonePosts(activeQQ); setPosts(d.posts); } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const handlePost = async () => {
    if (!activeQQ) return;
    try {
      const r = await qzoneManualPost(activeQQ, content || undefined);
      alert(r.content);
      setContent("");
      load();
    } catch { alert("发帖失败"); }
  };

  if (!activeQQ) return <div className="empty-state">请先选择实例</div>;

  return (
    <div className="main-content">
      <div className="panel">
        <div className="panel-header">
          <h2>QQ 空间说说</h2>
          <span className="convo-tag private">{posts.length} 条</span>
        </div>
        <div className="form-group">
          <label>手动发帖（留空自动生成）</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input style={{ flex: 1 }} placeholder="输入说说内容..." value={content}
              onChange={(e) => setContent(e.target.value)} />
            <button className="btn-primary" onClick={handlePost}>发帖</button>
          </div>
        </div>
        {loading ? <p className="text-dim">加载中...</p> : posts.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无说说</p>
        ) : (
          posts.map((p, i) => (
            <div key={i} className="chat-msg user" style={{ marginBottom: 8 }}>
              <div className="msg-text">{p.content}</div>
              <div className="msg-meta" style={{ marginTop: 4 }}>{fmtTime(p.created)}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
