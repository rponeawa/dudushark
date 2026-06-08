import { useState, useEffect } from "react";
import { getPausedGroups, resumeGroup } from "../api";

interface Props { activeQQ: string; }

export default function PausedGroupsPage({ activeQQ }: Props) {
  const [groups, setGroups] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { const d = await getPausedGroups(activeQQ); setGroups(d.paused_groups); } catch {}
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
    <div className="main-content">
      <div className="page-header">
        <h2>暂停的群聊</h2>
        <span className="badge">{groups.length} 个</span>
        <button className="btn-ghost btn-sm" onClick={load} style={{ marginLeft: 12 }}>刷新</button>
      </div>
      {loading ? <p className="dim">加载中...</p> : groups.length === 0 ? (
        <div className="empty-state">暂无被暂停的群聊。管理员发送 /pause 可暂停群消息</div>
      ) : (
        <div className="list">
          {groups.map((g) => (
            <div key={g} className="list-item" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span className="list-item-title">群 {g}</span>
              <button className="btn-primary btn-sm" onClick={() => handleResume(g)}>恢复</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
