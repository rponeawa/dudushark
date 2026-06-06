import { useState, useEffect } from "react";
import { getSystemStatus, getInstanceStatus, getReminders, SystemStatus, InstanceDetailStatus, MoodState, Reminder } from "../api";

function fmtUptime(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const SLEEP_LABELS: Record<string, string> = {
  awake: "清醒",
  sleepy: "困了",
  just_woke: "刚睡醒",
  night_owl: "夜猫子",
  daydream: "白日梦",
};

const SLEEP_ICON: Record<string, string> = {
  awake: "routine",
  sleepy: "bedtime",
  just_woke: "visibility",
  night_owl: "dark_mode",
  daydream: "cloud",
};

function MoodCard({ mood }: { mood: MoodState }) {
  const energyPct = Math.round(mood.energy * 100);
  let energyColor = "var(--green)";
  if (mood.energy < 0.2) energyColor = "var(--text-dim)";
  else if (mood.energy < 0.4) energyColor = "var(--yellow)";
  else if (mood.energy > 0.7) energyColor = "var(--accent)";

  return (
    <div className="stat-card" style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span className="material-symbols-outlined" style={{ fontSize: "1.4rem", color: "var(--text-dim)" }}>
        {SLEEP_ICON[mood.sleep_state]}
      </span>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: "0.85rem", fontWeight: 600 }}>
          {SLEEP_LABELS[mood.sleep_state] || mood.sleep_state}
        </div>
        <div style={{
          height: 5, background: "var(--bg-hover)", borderRadius: 3,
          overflow: "hidden", marginTop: 4,
        }}>
          <div style={{
            height: "100%", width: `${energyPct}%`,
            background: energyColor, borderRadius: 3,
            transition: "width 0.5s",
          }} />
        </div>
      </div>
      <span style={{ fontSize: "0.78rem", color: "var(--text-dim)", whiteSpace: "nowrap" }}>
        {energyPct}%
      </span>
    </div>
  );
}

export default function Status() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [detailQQ, setDetailQQ] = useState<string>("");
  const [detail, setDetail] = useState<InstanceDetailStatus | null>(null);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);

  const refreshStatus = async () => {
    try {
      const s = await getSystemStatus();
      setStatus(s);
    } catch { /* server not ready */ }
    setLoading(false);
  };

  const refreshDetail = async (qq: string) => {
    setDetailQQ(qq);
    try {
      const d = await getInstanceStatus(qq);
      setDetail(d);
    } catch {
      setDetail(null);
    }
  };

  useEffect(() => {
    refreshStatus();
    const t = setInterval(refreshStatus, 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (status?.instances.length && !detailQQ) {
      refreshDetail(status.instances[0].qq);
    }
  }, [status]);

  useEffect(() => {
    if (!detailQQ) return;
    getReminders(detailQQ).then((d) => setReminders(d.reminders)).catch(() => setReminders([]));
    const t = setInterval(() => {
      getReminders(detailQQ).then((d) => setReminders(d.reminders)).catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, [detailQQ]);

  if (loading) return <div className="empty-state">加载中...</div>;

  return (
    <div>
      {/* System Overview */}
      <div className="panel">
        <div className="panel-header">
          <h2>系统状态</h2>
          <span style={{ fontSize: "0.82rem", color: "var(--text-dim)" }}>
            运行 {status ? fmtUptime(status.uptime) : "-"}
          </span>
        </div>
        <div className="stat-grid">
          <div className="stat-card">
            <div className="stat-label">LLM 连接</div>
            <div className="stat-value" style={{ color: status?.llm_ok ? "var(--green)" : "var(--red)" }}>
              {status?.llm_ok ? "正常" : "异常"}
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">实例数</div>
            <div className="stat-value">{status?.instances.length ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">活跃对话</div>
            <div className="stat-value">{status?.total_conversations ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">记忆总数</div>
            <div className="stat-value">{status?.total_memories ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">内存占用</div>
            <div className="stat-value">{status?.memory_mb ?? 0} MB</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">平台</div>
            <div className="stat-value">{status?.platform ?? "-"}</div>
          </div>
        </div>
      </div>

      {/* Instance Cards */}
      {status?.instances && status.instances.length > 0 && (
        <div className="panel">
          <div className="panel-header">
            <h2>实例</h2>
          </div>
          <div className="instance-grid">
            {status.instances.map((inst) => (
              <div
                key={inst.qq}
                className={`instance-card ${detailQQ === inst.qq ? "active" : ""}`}
                onClick={() => refreshDetail(inst.qq)}
              >
                <h3>{inst.qq}</h3>
                <div className="status-line">
                  <span className={`status-dot ${inst.connected ? "online" : "offline"}`} />
                  OneBot {inst.connected ? "已连接" : "未连接"}
                </div>
                <div className="status-line">
                  <span className={`status-dot ${inst.napcat_running ? "online" : "offline"}`} />
                  NapCat {inst.napcat_running ? "运行中" : "未运行"}
                </div>
                <div className="status-line" style={{ color: "var(--text-dim)", fontSize: "0.78rem", marginTop: 6 }}>
                  对话 {inst.conversation_count} 用户 {inst.memory_users}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {status?.instances.length === 0 && (
        <div className="panel">
          <div className="empty-state">还没有实例，去「实例」页面添加一个吧</div>
        </div>
      )}

      {/* Detail Panel */}
      {detail && (
        <div className="panel">
          <div className="panel-header">
            <h2>实例 {detail.qq}</h2>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>连接状态</label>
              <span className={`status-dot ${detail.connected ? "online" : "offline"}`} />
              {detail.connected ? "已连接" : "未连接"}
            </div>
            <div className="form-group">
              <label>NapCat</label>
              <span className={`status-dot ${detail.napcat_running ? "online" : "offline"}`} />
              {detail.napcat_running ? "运行中" : "未运行"}
            </div>
            <div className="form-group">
              <label>NapCat WebUI</label>
              <span className="text-mono">127.0.0.1:{detail.napcat_webui_port}</span>
            </div>
          </div>
          {detail.mood && (
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label>心情</label>
              <MoodCard mood={detail.mood} />
            </div>
          )}
          <div className="form-row">
            <div className="form-group">
              <label>活跃对话</label>
              <span>{detail.conversation_count}</span>
            </div>
            <div className="form-group">
              <label>记忆用户</label>
              <span>{detail.memory_users.length}</span>
            </div>
            <div className="form-group">
              <label>记忆条目</label>
              <span>{detail.total_memories}</span>
            </div>
          </div>
          {detail.memory_users.length > 0 && (
            <div className="form-group">
              <label>各用户记忆数</label>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {Object.entries(detail.memory_stats).map(([uid, count]) => (
                  <span key={uid} className="mem-cat">
                    {uid}: {count}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Recent Events */}
      {status?.recent_events && status.recent_events.length > 0 && (
        <div className="panel">
          <div className="panel-header">
            <h2>最近事件</h2>
          </div>
          <div style={{ maxHeight: 300, overflowY: "auto" }}>
            {[...status.recent_events].reverse().map((evt, i) => (
              <div key={i} style={{
                padding: "6px 0", borderBottom: "1px solid var(--border)",
                fontSize: "0.82rem", display: "flex", gap: 10,
              }}>
                <span style={{ color: "var(--text-dim)", whiteSpace: "nowrap", fontFamily: "monospace", fontSize: "0.76rem" }}>
                  {fmtTime(evt._ts as number)}
                </span>
                <span style={{
                  padding: "1px 6px", borderRadius: 3, fontSize: "0.7rem",
                  background: evt.type === "message" ? "var(--accent-dim)" : "var(--bg-hover)",
                  color: evt.type === "message" ? "var(--accent)" : "var(--text-dim)",
                  whiteSpace: "nowrap",
                }}>
                  {evt.type}
                </span>
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {evt.type === "message" ? `${evt.user_name}: ${(evt.text as string)?.slice(0, 60)} → ${(evt.reply as string)?.slice(0, 40)}` : `${evt.type} qq=${evt.qq}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {reminders.length > 0 && (
        <div className="panel">
          <div className="panel-header">
            <h2>定时提醒 ({reminders.length})</h2>
          </div>
          <div className="mem-list">
            {reminders.map((r, i) => {
              const d = new Date(r.at_utc * 1000);
              const now = new Date();
              const diff = Math.floor((d.getTime() - now.getTime()) / 60000);
              return (
                <div key={i} className="mem-item">
                  <div className="mem-header">
                    <span className="mem-cat">{diff > 0 ? `${diff}分钟后` : "即将"}</span>
                    <span className="mem-title">{d.toLocaleString("zh-CN")}</span>
                  </div>
                  <div className="mem-content">
                    {r.group_id ? `群聊 ${r.group_id}: ` : `私聊 ${r.user_id}: `}{r.content}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
