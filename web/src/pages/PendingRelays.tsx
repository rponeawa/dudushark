import { useState, useEffect } from "react";

interface PendingRelay {
  id: string; from_role: string; to_role: string; content: string;
  voice: string | null; send_at: number; created_at: number;
}

interface Props { activeQQ: string; token?: string; }

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
}

export default function PendingRelaysPage({ activeQQ, token }: Props) {
  const [relays, setRelays] = useState<PendingRelay[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    const t = token || localStorage.getItem("token") || "";
    setLoading(true);
    try {
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
    const t = token || localStorage.getItem("token") || "";
    await fetch(`/api/instances/${activeQQ}/pending_relays/${id}`, {
      method: "DELETE",
      headers: { "Authorization": `Bearer ${t}` },
    });
    load();
  };

  return (
    <div className="main-content">
      <div className="page-header">
        <h2>待发送代传话</h2>
        <span className="badge">{relays.length} 条</span>
      </div>
      {loading ? <p className="dim">加载中...</p> : relays.length === 0 ? <p className="dim">暂无待发送的代传话</p> : (
        <div className="list">
          {relays.filter((r: any) => !r.sent).map((r: any) => (
            <div key={r.id} className="list-item" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 500 }}>【{r.from_role}】→ 【{r.to_role}】</div>
                <div style={{ marginTop: 4 }}>{r.content}</div>
                <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
                  发送时间: {fmtTime(r.send_at)} {r.voice ? " | 语音" : ""}
                </div>
              </div>
              <button className="btn-danger btn-sm" onClick={() => cancelRelay(r.id)}>取消</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
