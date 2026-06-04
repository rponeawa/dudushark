import { useState } from "react";
import {
  createInstance,
  startInstance,
  stopInstance,
  getQrCode,
  InstanceInfo,
} from "../api";

interface Props {
  instances: InstanceInfo[];
  activeQQ: string;
  setActiveQQ: (qq: string) => void;
  refresh: () => void;
}

export default function Instances({ instances, activeQQ, setActiveQQ, refresh }: Props) {
  const [newQQ, setNewQQ] = useState("");
  const [napcatPath, setNapcatPath] = useState("");
  const [qrQQ, setQrQQ] = useState<string | null>(null);
  const [qrData, setQrData] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");

  const toast = (text: string, ok = true) => {
    setMsg(text);
    setTimeout(() => setMsg(""), 2500);
  };

  const handleCreate = async () => {
    if (!newQQ.trim()) return;
    setLoading(true);
    try {
      await createInstance(newQQ.trim(), napcatPath.trim());
      toast("实例已创建");
      setNewQQ("");
      refresh();
    } catch (e) {
      toast("创建失败", false);
    }
    setLoading(false);
  };

  const handleStart = async (qq: string) => {
    setLoading(true);
    try {
      await startInstance(qq);
      toast(`实例 ${qq} 已启动`);
      setTimeout(refresh, 2000);
    } catch (e) {
      toast("启动失败", false);
    }
    setLoading(false);
  };

  const handleStop = async (qq: string) => {
    setLoading(true);
    try {
      await stopInstance(qq);
      toast(`实例 ${qq} 已停止`);
      refresh();
    } catch (e) {
      toast("停止失败", false);
    }
    setLoading(false);
  };

  const handleShowQR = async (qq: string) => {
    setQrQQ(qq);
    setQrData(null);
    try {
      const data = await getQrCode(qq);
      setQrData(data.qrcode);
    } catch {
      setQrData(null);
    }
  };

  return (
    <div>
      {msg && <div className={`toast show ${msg.includes("失败") ? "err" : "ok"}`}>{msg}</div>}

      <div className="panel">
        <div className="panel-header">
          <h2>添加新实例</h2>
        </div>
        <div className="form-row" style={{ alignItems: "flex-end" }}>
          <div className="form-group">
            <label>QQ 号</label>
            <input
              value={newQQ}
              onChange={(e) => setNewQQ(e.target.value)}
              placeholder="输入 QQ 号"
            />
          </div>
          <div className="form-group">
            <label>NapCatQQ 路径 (可选)</label>
            <input
              value={napcatPath}
              onChange={(e) => setNapcatPath(e.target.value)}
              placeholder="留空则自动检测"
            />
          </div>
          <div className="form-group" style={{ flex: "0 0 auto" }}>
            <button className="btn-primary" onClick={handleCreate} disabled={loading}>
              创建实例
            </button>
          </div>
        </div>
      </div>

      {qrQQ && (
        <div className="panel">
          <div className="panel-header">
            <h2>扫码登录: {qrQQ}</h2>
            <button className="btn-ghost btn-sm" onClick={() => setQrQQ(null)}>关闭</button>
          </div>
          <div className="qr-box">
            <div className="qr-placeholder">
              {qrData ? (
                <img src={`data:image/png;base64,${qrData}`} alt="QR Code" style={{ width: "100%", height: "100%", objectFit: "contain" }} />
              ) : (
                "等待二维码..."
              )}
            </div>
            <p className="text-dim">
              打开 NapCatQQ WebUI 查看二维码: <code>http://127.0.0.1:6099/webui/</code>
            </p>
          </div>
        </div>
      )}

      <div className="instance-grid">
        {instances.map((inst) => (
          <div
            key={inst.qq}
            className={`instance-card ${activeQQ === inst.qq ? "active" : ""}`}
            onClick={() => setActiveQQ(inst.qq)}
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
            <div className="card-actions">
              {!inst.connected && (
                <button className="btn-primary btn-sm" onClick={(e) => { e.stopPropagation(); handleStart(inst.qq); }} disabled={loading}>
                  启动
                </button>
              )}
              {inst.napcat_running && (
                <button className="btn-danger btn-sm" onClick={(e) => { e.stopPropagation(); handleStop(inst.qq); }} disabled={loading}>
                  停止
                </button>
              )}
              <button className="btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); handleShowQR(inst.qq); }}>
                查看二维码
              </button>
            </div>
          </div>
        ))}

        {instances.length === 0 && (
          <div className="empty-state" style={{ gridColumn: "1/-1" }}>
            还没有实例，在上方添加一个 QQ 号开始吧～
          </div>
        )}
      </div>
    </div>
  );
}
