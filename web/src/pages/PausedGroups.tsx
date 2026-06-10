import { useState, useEffect } from "react";
import { getPausedGroups, resumeGroup } from "../api";

interface Props { activeQQ: string; }

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function PausedGroupsPage({ activeQQ }: Props) {
  const [groups, setGroups] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [pausedUsers, setPausedUsers] = useState<Record<string, number>>({});
  const token = localStorage.getItem("dudushark_token") || "";

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { const d = await getPausedGroups(activeQQ); setGroups(d.paused_groups); } catch {}
    try {
      const r = await fetch(`/api/instances/${activeQQ}/proactive_paused`, {
        headers: { "Authorization": `Bearer ${token}` },
      });
      const d = await r.json();
      setPausedUsers(d.paused || {});
    } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const handleResume = async (gid: string) => {
    if (!confirm(`恢复群 ${gid} 的消息处理？`)) return;
    await resumeGroup(activeQQ, gid);
    load();
  };

  const handleUnpause = async (uid: string) => {
    await fetch(`/api/instances/${activeQQ}/proactive_paused/${uid}`, {
      method: "DELETE", headers: { "Authorization": `Bearer ${token}` },
    });
    load();
  };

  if (!activeQQ) return <div className="empty-state">请先选择实例</div>;

  const pausedList = Object.entries(pausedUsers).filter(([, until]) => until > Date.now() / 1000);

  return (
    <div>
      <div className="panel">
        <div className="panel-header">
          <h2>暂停的群聊</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="convo-tag private">{groups.length} 个</span>
            <button className="btn-ghost btn-sm" onClick={load}>刷新</button>
          </div>
        </div>
        {loading ? <p className="text-dim">加载中...</p> : groups.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无被暂停的群聊。管理员发送 /pause 可暂停群消息</p>
        ) : (
          groups.map((g) => (
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

      <div className="panel">
        <div className="panel-header">
          <h2>主动消息暂停</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="convo-tag private">{pausedList.length} 人</span>
            <button className="btn-ghost btn-sm" onClick={load}>刷新</button>
          </div>
        </div>
        {loading ? <p className="text-dim">加载中...</p> : pausedList.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无暂停。嘟嘟会在对方说"去睡了"/"在忙"时自动暂停</p>
        ) : (
          pausedList.map(([uid, until]) => (
            <div key={uid} className="stat-card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div>
                <div className="stat-label">用户 ID</div>
                <div className="stat-value" style={{ fontFamily: "SF Mono, Monaco, monospace", fontSize: ".9rem" }}>{uid}</div>
                <div className="stat-label" style={{ marginTop: 4 }}>恢复时间: {fmtTime(until)}</div>
              </div>
              <button className="btn-primary btn-sm" onClick={() => handleUnpause(uid)}>恢复</button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
