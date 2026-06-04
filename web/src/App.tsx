import { useState, useEffect, useCallback } from "react";
import { Routes, Route, useNavigate, useLocation } from "react-router-dom";
import Status from "./pages/Status";
import Instances from "./pages/Instances";
import Conversations from "./pages/Conversations";
import Memories from "./pages/Memories";
import Settings from "./pages/Settings";
import { listInstances, InstanceInfo } from "./api";

type Tab = { label: string; path: string };

const TABS: Tab[] = [
  { label: "状态", path: "/" },
  { label: "实例管理", path: "/instances" },
  { label: "对话", path: "/conversations" },
  { label: "记忆", path: "/memories" },
  { label: "模型设置", path: "/settings" },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [instances, setInstances] = useState<InstanceInfo[]>([]);
  const [activeQQ, setActiveQQ] = useState<string>("");
  const [globalStatus, setGlobalStatus] = useState("未连接");

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
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <button className="logo" onClick={() => navigate("/")}>
            🦈 嘟嘟鲨鱼 <span className="logo-sub">DuduShark</span>
          </button>
          <nav className="nav-tabs">
            {TABS.map((t) => (
              <button
                key={t.path}
                className={t.path === activeTab.path ? "active" : ""}
                onClick={() => navigate(t.path)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="topbar-status">{globalStatus}</div>
        </div>
      </header>

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
    </>
  );
}
