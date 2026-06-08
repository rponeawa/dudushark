import { useState, useEffect } from "react";

interface Props { activeQQ: string; }

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function PendingRelaysPage({ activeQQ }: Props) {
  const [relays, setRelays] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try {
      const t = localStorage.getItem("token") || "";
      const r = await fetch(`/api/instances/${activeQQ}/pending_relays`, {
        headers: { "Authorization": `Bearer ${t}` },
      });
      const d = await r.json();
      setRelays(d.pending_relays || []);
    } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const cancelRelay = async (id: string) => {
    if (!confirm("取消这条代传话？")) return;
    const t = localStorage.getItem("token") || "";
    await fetch(`/api/instances/${activeQQ}/pending_relays/${id}`, {
      method: "DELETE", headers: { "Authorization": `Bearer ${t}` },
    });
    load();
  };

  if (!activeQQ) return <div className="empty-state">请先选择实例</div>;

  const pending = relays.filter((r) => !r.sent);

  return (
    <div className="main-content">
      <div className="panel">
        <div className="panel-header">
          <h2>待发送代传话</h2>
          <span className="convo-tag private">{pending.length} 条</span>
          <button className="btn-ghost btn-sm" onClick={load}>刷新</button>
        </div>
        {loading ? <p className="text-dim">加载中...</p> : pending.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无待发送的代传话</p>
        ) : (
          pending.map((r) => (
            <div key={r.id} className="mem-item" style={{ marginBottom: 8 }}>
              <div className="mem-header">
                <span className="mem-cat">{r.from_role}</span>
                <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--text-dim)" }}>arrow_forward</span>
                <span className="mem-cat">{r.to_role}</span>
              </div>
              <div className="mem-content" style={{ marginBottom: 8 }}>{r.content}</div>
              <div className="mem-date" style={{ marginBottom: 8 }}>
                发送: {fmtTime(r.send_at)} {r.voice ? "· 语音" : ""}
              </div>
              <button className="btn-danger btn-sm" onClick={() => cancelRelay(r.id)}>取消</button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
