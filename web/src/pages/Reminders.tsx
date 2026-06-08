import { useState, useEffect } from "react";
import { getReminders, Reminder } from "../api";

interface Props { activeQQ: string; }

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function RemindersPage({ activeQQ }: Props) {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!activeQQ) return;
    setLoading(true);
    try { const d = await getReminders(activeQQ); setReminders(d.reminders); } catch {}
    setLoading(false);
  };
  useEffect(() => { load(); }, [activeQQ]);

  if (!activeQQ) return <div className="empty-state">请先选择实例</div>;

  return (
    <div className="main-content">
      <div className="page-header">
        <h2>定时提醒</h2>
        <span className="badge">{reminders.length} 条</span>
        <button className="btn-ghost btn-sm" onClick={load} style={{ marginLeft: 12 }}>刷新</button>
      </div>
      {loading ? <p className="dim">加载中...</p> : reminders.length === 0 ? (
        <div className="empty-state">暂无定时提醒。嘟嘟会在别人让她提醒时自动创建</div>
      ) : (
        <div className="list">
          {reminders.map((r, i) => (
            <div key={i} className="list-item">
              <div className="list-item-title">{r.content}</div>
              <div className="list-item-meta">
                发送: {fmtTime(r.at_utc)} · {r.group_id ? `群 ${r.group_id}` : `用户 ${r.user_id}`}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
