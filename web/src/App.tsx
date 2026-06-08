import { useState, useEffect, useCallback } from "react";
import { Routes, Route, useNavigate, useLocation } from "react-router-dom";
import Status from "./pages/Status";
import Instances from "./pages/Instances";
import Conversations from "./pages/Conversations";
import Memories from "./pages/Memories";
import Settings from "./pages/Settings";
import Stickers from "./pages/Stickers";
import { listInstances, InstanceInfo, login, setToken, getToken } from "./api";

type Tab = { label: string; path: string; icon: string };

const TABS: Tab[] = [
  { label: "状态", path: "/", icon: "monitoring" },
  { label: "实例", path: "/instances", icon: "dns" },
  { label: "对话", path: "/conversations", icon: "chat" },
  { label: "记忆", path: "/memories", icon: "psychology" },
  { label: "表情包", path: "/stickers", icon: "gif_box" },
  { label: "设置", path: "/settings", icon: "settings" },
];

function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const handle = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pw) return;
    setLoading(true);
    setErr("");
    try {
      const res = await login(pw);
      setToken(res.token || "ok");
      onLogin();
    } catch {
      setErr("密码错误");
    }
    setLoading(false);
  };

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={handle}>
        <div className="auth-icon">🦈</div>
        <h1>嘟嘟鲨鱼</h1>
        <p className="auth-sub">DuduShark WebUI</p>
        <input
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="请输入面板密码"
          autoFocus
        />
        <button className="btn-primary" type="submit" disabled={loading}>
          {loading ? "验证中..." : "登录"}
        </button>
        {err && <div className="auth-error">{err}</div>}
      </form>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [instances, setInstances] = useState<InstanceInfo[]>([]);
  const [activeQQ, setActiveQQ] = useState<string>("");
  const [globalStatus, setGlobalStatus] = useState("未连接");
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 700);
  const [authed, setAuthed] = useState(() => !!getToken());

  const activeTab = TABS.find((t) => t.path === location.pathname) ?? TABS[0];

  const refreshInstances = useCallback(async () => {
    try {
      const data = await listInstances();
      setInstances(data.instances);
      setActiveQQ((prev) => {
        if (!prev && data.instances.length > 0) return data.instances[0].qq;
        return prev;
      });
      const anyConnected = data.instances.some((i) => i.connected);
      setGlobalStatus(anyConnected ? "已连接" : "未连接");
    } catch (e: unknown) {
      if ((e as Error).message === "unauthorized") {
        setAuthed(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    refreshInstances();
    const timer = setInterval(refreshInstances, 5000);
    return () => clearInterval(timer);
  }, [refreshInstances, authed]);

  useEffect(() => {
    if (!authed) return;
    const ws = new WebSocket(`ws://${window.location.hostname}:8080/api/ws/widget`);
    ws.onmessage = () => refreshInstances();
    return () => ws.close();
  }, [refreshInstances, authed]);

  const handleLogout = () => {
    setToken(null);
    setAuthed(false);
  };

  const handleLogin = () => setAuthed(true);

  if (!authed) return <LoginPage onLogin={handleLogin} />;

  return (
    <div className="app-layout">
      {sidebarOpen && (
        <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />
      )}

      <header className="topbar">
        <button className="topbar-btn" onClick={() => setSidebarOpen((v) => !v)}>☰</button>
        <span className="topbar-title">🦈 嘟嘟鲨鱼</span>
      </header>

      <aside className={`sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="sidebar-top">
          <button className="sidebar-logo" onClick={() => { navigate("/"); if (window.innerWidth <= 700) setSidebarOpen(false); }}>
            <span className="sidebar-logo-icon">🦈</span>
            <span className="sidebar-logo-text">嘟嘟鲨鱼</span>
          </button>
          <nav className="sidebar-nav">
            {TABS.map((t) => (
              <button
                key={t.path}
                className={`sidebar-nav-item${t.path === activeTab.path ? " active" : ""}`}
                onClick={() => { navigate(t.path); if (window.innerWidth <= 700) setSidebarOpen(false); }}
              >
                <span className="material-symbols-outlined sidebar-nav-icon">{t.icon}</span>
                <span className="sidebar-nav-label">{t.label}</span>
              </button>
            ))}
          </nav>
        </div>
        <div className="sidebar-footer">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className={`status-dot ${globalStatus === "已连接" ? "online" : "offline"}`} />
            <span className="sidebar-status-text">{globalStatus}</span>
          </div>
          <button className="sidebar-logout" onClick={handleLogout}>退出登录</button>
        </div>
      </aside>

      <main className="main-content">
        <Routes>
          <Route path="/" element={<Status />} />
          <Route
            path="/instances"
            element={
              <Instances
                instances={instances}
                activeQQ={activeQQ}
                setActiveQQ={setActiveQQ}
                refresh={refreshInstances}
              />
            }
          />
          <Route
            path="/conversations"
            element={<Conversations instances={instances} activeQQ={activeQQ} setActiveQQ={setActiveQQ} />}
          />
          <Route
            path="/memories"
            element={<Memories instances={instances} activeQQ={activeQQ} setActiveQQ={setActiveQQ} />}
          />
          <Route
            path="/stickers"
            element={<Stickers activeQQ={activeQQ} />}
          />
          <Route
            path="/settings"
            element={<Settings instances={instances} activeQQ={activeQQ} setActiveQQ={setActiveQQ} />}
          />
        </Routes>
      </main>
    </div>
  );
}
