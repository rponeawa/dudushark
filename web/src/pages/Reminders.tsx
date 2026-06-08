import { useState, useEffect } from "react";
import { getReminders, Reminder } from "../api";

interface Props { activeQQ: string; }

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
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

  return (
    <div className="main-content">
      <div className="page-header">
        <h2>定时提醒</h2>
        <span className="badge">{reminders.length} 条</span>
      </div>
      {loading ? <p className="dim">加载中...</p> : reminders.length === 0 ? <p className="dim">暂无定时提醒</p> : (
        <div className="list">
          {reminders.map((r, i) => (
            <div key={i} className="list-item" style={{ display: "flex", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontWeight: 500 }}>{r.content}</div>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  发送时间: {fmtTime(r.at_utc)} → 用户: {r.user_id}
                  {r.group_id ? ` (群: ${r.group_id})` : " (私聊)"}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
