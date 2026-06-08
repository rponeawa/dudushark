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
    <div>
      <div className="panel">
        <div className="panel-header">
          <h2>定时提醒</h2>
          <span className="convo-tag private">{reminders.length} 条</span>
          <button className="btn-ghost btn-sm" onClick={load}>刷新</button>
        </div>
        {loading ? <p className="text-dim">加载中...</p> : reminders.length === 0 ? (
          <p className="text-dim" style={{ padding: 12 }}>暂无定时提醒。嘟嘟会在别人让她提醒时自动创建</p>
        ) : (
          reminders.map((r, i) => (
            <div key={i} className="chat-msg user" style={{ marginBottom: 8 }}>
              <div className="msg-text">{r.content}</div>
              <div className="msg-meta" style={{ marginTop: 4 }}>
                发送: {fmtTime(r.at_utc)} · {r.group_id ? `群 ${r.group_id}` : `用户 ${r.user_id}`}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
