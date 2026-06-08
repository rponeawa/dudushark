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
  const [autoContent, setAutoContent] = useState("");

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { setPosts(await getQzonePosts(activeQQ)); } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const handlePost = async () => {
    if (!activeQQ) return;
    try {
      const r = await qzoneManualPost(activeQQ, autoContent || undefined);
      alert(r.content);
      setAutoContent("");
      load();
    } catch { alert("发帖失败"); }
  };

  return (
    <div className="main-content">
      <div className="page-header">
        <h2>QQ 空间说说</h2>
        <span className="badge">{posts.length} 条</span>
      </div>
      <div className="form-group" style={{ marginBottom: 16 }}>
        <label>手动发帖（留空自动生成）</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            style={{ flex: 1 }}
            placeholder="输入说说内容，留空自动生成..."
            value={autoContent}
            onChange={(e) => setAutoContent(e.target.value)}
          />
          <button className="btn-primary" onClick={handlePost}>发帖</button>
        </div>
      </div>
      {loading ? <p className="dim">加载中...</p> : posts.length === 0 ? <p className="dim">暂无说说</p> : (
        <div className="list">
          {posts.map((p, i) => (
            <div key={i} className="list-item">
              <div>{p.content}</div>
              <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>{fmtTime(p.created)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
