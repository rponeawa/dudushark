import { useState, useEffect } from "react";
import { getStickers, removeSticker, StickerEntry } from "../api";

interface Props {
  activeQQ: string;
}

export default function Stickers({ activeQQ }: Props) {
  const [stickers, setStickers] = useState<StickerEntry[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try {
      const d = await getStickers(activeQQ);
      setStickers(d.stickers);
      setCount(d.count);
    } catch { }
    setLoading(false);
  };

  useEffect(() => { load(); }, [activeQQ]);

  const handleRemove = async (id: number) => {
    if (!confirm("删除这个表情包？")) return;
    await removeSticker(activeQQ, id);
    load();
  };

  return (
    <div className="main-content">
      <div className="page-header">
        <h2>表情包收藏</h2>
        <span className="badge">{count} 个</span>
      </div>

      {loading ? (
        <p className="dim">加载中...</p>
      ) : stickers.length === 0 ? (
        <p className="dim">暂无收藏的表情包。嘟嘟遇到喜欢的表情会自己存下来～</p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
          {stickers.map((s) => (
            <div key={s.id} style={{
              border: "1px solid var(--border)", borderRadius: 8,
              padding: 12, background: "var(--bg-card)", position: "relative",
            }}>
              <img
                src={s.url}
                alt={s.description}
                style={{ width: "100%", height: 120, objectFit: "contain", borderRadius: 4, background: "var(--bg)" }}
                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
              />
              <p style={{ margin: "8px 0 4px", fontSize: 13 }}>{s.description}</p>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
                {s.tags.map((t, i) => (
                  <span key={i} style={{ fontSize: 11, padding: "1px 6px", borderRadius: 3, background: "var(--bg)", color: "var(--text-dim)" }}>{t}</span>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-dim)", display: "flex", justifyContent: "space-between" }}>
                <span>使用 {s.used_count} 次</span>
                <button className="btn-ghost btn-sm" onClick={() => handleRemove(s.id)} style={{ fontSize: 11, padding: "2px 8px" }}>删除</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
