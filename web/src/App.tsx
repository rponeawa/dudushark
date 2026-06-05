import { useState, useEffect, useCallback } from "react";
import { Routes, Route, useNavigate, useLocation } from "react-router-dom";
import Status from "./pages/Status";
import Instances from "./pages/Instances";
import Conversations from "./pages/Conversations";
import Memories from "./pages/Memories";
import Settings from "./pages/Settings";
import { listInstances, InstanceInfo } from "./api";

type Tab = { label: string; path: string; icon: string };

const TABS: Tab[] = [
  { label: "状态", path: "/", icon: "📊" },
  { label: "实例管理", path: "/instances", icon: "🖥" },
  { label: "对话", path: "/conversations", icon: "💬" },
  { label: "记忆", path: "/memories", icon: "🧠" },
  { label: "模型设置", path: "/settings", icon: "⚙" },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [instances, setInstances] = useState<InstanceInfo[]>([]);
  const [activeQQ, setActiveQQ] = useState<string>("");
  const [globalStatus, setGlobalStatus] = useState("未连接");
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.innerWidth > 700
  );

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
    } catch { /* server not ready */ }
  }, []);

  useEffect(() => {
    refreshInstances();
    const timer = setInterval(refreshInstances, 5000);
    return () => clearInterval(timer);
  }, [refreshInstances]);

  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.hostname}:8080/api/ws/widget`);
    ws.onmessage = () => refreshInstances();
    return () => ws.close();
  }, []);

  return (
    <div className="app-layout">
      {/* Sidebar overlay for mobile */}
      {sidebarOpen && (
        <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />
      )}

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
                <span className="sidebar-nav-icon">{t.icon}</span>
                <span className="sidebar-nav-label">{t.label}</span>
              </button>
            ))}
          </nav>
        </div>
        <div className="sidebar-footer">
          <span className={`status-dot ${globalStatus === "已连接" ? "online" : "offline"}`} />
          <span className="sidebar-status-text">{globalStatus}</span>
        </div>
      </aside>

      {/* Hamburger toggle */}
      <button
        className="sidebar-toggle"
        onClick={() => setSidebarOpen((v) => !v)}
        title={sidebarOpen ? "收起侧边栏" : "展开侧边栏"}
      >
        {sidebarOpen ? "✕" : "☰"}
      </button>

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
            path="/settings"
            element={<Settings instances={instances} activeQQ={activeQQ} setActiveQQ={setActiveQQ} />}
          />
        </Routes>
      </main>
    </div>
  );
}
