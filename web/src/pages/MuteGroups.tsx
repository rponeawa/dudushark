import { useState, useEffect } from "react";
import { getPausedGroups, resumeGroup } from "../api";

interface Props { activeQQ: string; }

export default function MuteGroupsPage({ activeQQ }: Props) {
  const [muted, setMuted] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { const d = await getPausedGroups(activeQQ); setMuted(d.paused_groups); } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const handleResume = async (gid: string) => {
    if (!confirm(`恢复群 ${gid} 的消息处理？`)) return;
    await resumeGroup(activeQQ, gid);
    load();
  };

  if (!activeQQ) return <div className="empty-state">请先选择实例</div>;

  return (
    <div>
      <div className="panel">
        <div className="panel-header">
          <h2>群聊免打扰</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="convo-tag private">{muted.length} 个</span>
            <button className="btn-ghost btn-sm" onClick={load}>刷新</button>
          </div>
        </div>
        <p className="text-dim" style={{ marginBottom: 16 }}>
          嘟嘟睡觉时被吵醒生气会自动开启免打扰。早上8点自动恢复。
        </p>
        {loading ? <p className="text-dim">加载中...</p> : muted.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无被免打扰的群聊</p>
        ) : (
          muted.map((g) => (
            <div key={g} className="stat-card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div>
                <div className="stat-label">群聊 ID</div>
                <div className="stat-value" style={{ fontFamily: "SF Mono, Monaco, monospace", fontSize: ".9rem" }}>{g}</div>
              </div>
              <button className="btn-primary btn-sm" onClick={() => handleResume(g)}>恢复</button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
