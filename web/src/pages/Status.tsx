import { useState, useEffect } from "react";
import { getSystemStatus, getInstanceStatus, SystemStatus, InstanceDetailStatus } from "../api";

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

export default function Status() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [detailQQ, setDetailQQ] = useState<string>("");
  const [detail, setDetail] = useState<InstanceDetailStatus | null>(null);
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

  if (loading) return <div className="empty-state">加载中...</div>;

  return (
    <div>
      {/* System Overview */}
      <div className="panel">
        <div className="panel-header">
          <h2>系统状态</h2>
          <span className="text-dim" style={{ fontSize: "0.82rem" }}>
            运行时间: {status ? fmtUptime(status.uptime) : "-"}
          </span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "10px" }}>
          <StatusCard label="LLM 连接" value={status?.llm_ok ? "正常" : "异常"} ok={status?.llm_ok} />
          <StatusCard label="实例数" value={String(status?.instances.length ?? 0)} />
          <StatusCard label="活跃对话" value={String(status?.total_conversations ?? 0)} />
          <StatusCard label="记忆总数" value={String(status?.total_memories ?? 0)} />
          <StatusCard label="内存占用" value={`${status?.memory_mb ?? 0} MB`} />
          <StatusCard label="平台" value={status?.platform ?? "-"} />
        </div>
      </div>

      {/* Instance Cards */}
      {status?.instances && status.instances.length > 0 && (
        <div className="panel">
          <div className="panel-header">
            <h2>实例状态</h2>
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
                  OneBot: {inst.connected ? "已连接" : "未连接"}
                </div>
                <div className="status-line">
                  <span className={`status-dot ${inst.napcat_running ? "online" : "offline"}`} />
                  NapCat: {inst.napcat_running ? "运行中" : "未运行"}
                </div>
                <div className="status-line text-dim" style={{ marginTop: 4 }}>
                  对话 {inst.conversation_count} · 用户 {inst.memory_users}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {status?.instances.length === 0 && (
        <div className="panel">
          <div className="empty-state">
            还没有实例 —— 去「实例管理」添加一个吧～
          </div>
        </div>
      )}

      {/* Detail Panel */}
      {detail && (
        <div className="panel">
          <div className="panel-header">
            <h2>实例 {detail.qq} 详情</h2>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>连接状态</label>
              <span className={`status-dot ${detail.connected ? "online" : "offline"}`} />
              {detail.connected ? "已连接" : "未连接"}
            </div>
            <div className="form-group">
              <label>NapCat 状态</label>
              <span className={`status-dot ${detail.napcat_running ? "online" : "offline"}`} />
              {detail.napcat_running ? "运行中" : "未运行"}
            </div>
            <div className="form-group">
              <label>NapCat WebUI</label>
              <span className="text-mono">http://127.0.0.1:{detail.napcat_webui_port}/webui/</span>
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>活跃对话数</label>
              <span>{detail.conversation_count}</span>
            </div>
            <div className="form-group">
              <label>记忆用户数</label>
              <span>{detail.memory_users.length}</span>
            </div>
            <div className="form-group">
              <label>记忆条目总数</label>
              <span>{detail.total_memories}</span>
            </div>
          </div>
          {detail.memory_users.length > 0 && (
            <div className="form-group">
              <label>各用户记忆数</label>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
                {Object.entries(detail.memory_stats).map(([uid, count]) => (
                  <span key={uid} className="mem-cat" style={{ fontSize: "0.78rem" }}>
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
                padding: "6px 0",
                borderBottom: "1px solid var(--border)",
                fontSize: "0.82rem",
                display: "flex",
                gap: 10,
              }}>
                <span style={{ color: "var(--text-dim)", whiteSpace: "nowrap", fontFamily: "monospace" }}>
                  {fmtTime(evt._ts as number)}
                </span>
                <span style={{
                  padding: "1px 6px",
                  borderRadius: 3,
                  fontSize: "0.72rem",
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
    </div>
  );
}

function StatusCard({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div style={{
      background: "var(--bg-card)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      padding: "12px 14px",
    }}>
      <div style={{ fontSize: "0.78rem", color: "var(--text-dim)", marginBottom: 4 }}>{label}</div>
      <div style={{
        fontSize: "1.05rem",
        fontWeight: 600,
        color: ok === false ? "var(--red)" : ok === true ? "var(--green)" : "var(--text-bright)",
      }}>
        {value}
      </div>
    </div>
  );
}
