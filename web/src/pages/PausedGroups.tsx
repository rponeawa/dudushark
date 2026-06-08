import { useState, useEffect } from "react";
import { getPausedGroups, resumeGroup } from "../api";

interface Props { activeQQ: string; }

export default function PausedGroupsPage({ activeQQ }: Props) {
  const [groups, setGroups] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { setGroups(await getPausedGroups(activeQQ)); } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  const handleResume = async (gid: string) => {
    if (!confirm(`恢复群 ${gid} 的消息处理？`)) return;
    await resumeGroup(activeQQ, gid);
    load();
  };

  return (
    <div className="main-content">
      <div className="page-header">
        <h2>暂停的群聊</h2>
        <span className="badge">{groups.length} 个</span>
      </div>
      {loading ? <p className="dim">加载中...</p> : groups.length === 0 ? <p className="dim">暂无被暂停的群聊</p> : (
        <div className="list">
          {groups.map((g) => (
            <div key={g} className="list-item" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontWeight: 500 }}>群 {g}</span>
              <button className="btn-primary btn-sm" onClick={() => handleResume(g)}>恢复</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
