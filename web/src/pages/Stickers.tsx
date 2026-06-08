import { useState, useEffect } from "react";
import { getStickers, removeSticker, StickerEntry } from "../api";

interface Props { activeQQ: string; }

export default function Stickers({ activeQQ }: Props) {
  const [stickers, setStickers] = useState<StickerEntry[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { const d = await getStickers(activeQQ); setStickers(d.stickers); setCount(d.count); } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const handleRemove = async (id: number) => {
    if (!confirm("删除这个表情包？")) return;
    await removeSticker(activeQQ, id);
    load();
  };

  if (!activeQQ) return <div className="empty-state">请先选择实例</div>;

  return (
    <div>
      <div className="panel">
        <div className="panel-header">
          <h2>表情包收藏</h2>
          <span className="convo-tag private">{count} 个</span>
          <button className="btn-ghost btn-sm" onClick={load}>刷新</button>
        </div>
        {loading ? <p className="text-dim">加载中...</p> : stickers.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无收藏的表情包。嘟嘟遇到喜欢的表情会自己存下来～</p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 12 }}>
            {stickers.map((s) => (
              <div key={s.id} className="stat-card" style={{ padding: 10 }}>
                <img src={s.url} alt={s.description}
                  style={{ width: "100%", height: 130, objectFit: "contain", borderRadius: 6, background: "var(--bg)" }}
                  onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                <div style={{ marginTop: 8, fontSize: 13 }}>{s.description}</div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", margin: "6px 0" }}>
                  {s.tags.map((t, i) => (
                    <span key={i} className="mem-cat" style={{ fontSize: 10 }}>{t}</span>
                  ))}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="text-dim" style={{ fontSize: 11 }}>使用 {s.used_count} 次</span>
                  <button className="btn-danger btn-sm" onClick={() => handleRemove(s.id)}>删除</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
