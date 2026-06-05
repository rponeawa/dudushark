import { useState, useEffect } from "react";
import { getConfig, updateConfig, InstanceInfo, BotConfig } from "../api";

interface Props {
  instances: InstanceInfo[];
  activeQQ: string;
  setActiveQQ: (qq: string) => void;
}

const DEFAULT_LLM = {
  base_url: "https://api.stepfun.com/v1/chat/completions",
  model: "step-3.5-flash-2603",
  api_key: "",
};

export default function Settings({ instances, activeQQ, setActiveQQ }: Props) {
  const [cfg, setCfg] = useState<BotConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!activeQQ) return;
    getConfig(activeQQ)
      .then(setCfg)
      .catch(() => {});
  }, [activeQQ]);

  const toast = (text: string, ok = true) => {
    setMsg(text);
    setTimeout(() => setMsg(""), 2500);
  };

  const handleSave = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      await updateConfig(activeQQ, cfg);
      toast("保存成功");
    } catch {
      toast("保存失败", false);
    }
    setSaving(false);
  };

  if (instances.length === 0) {
    return <div className="empty-state">请先在「实例管理」中创建并启动一个实例～</div>;
  }

  if (!cfg) {
    return <div className="empty-state">加载中...</div>;
  }

  return (
    <div>
      {msg && <div className={`toast show ${msg.includes("失败") ? "err" : "ok"}`}>{msg}</div>}

      <div className="panel">
        <div className="panel-header">
          <h2>选择实例</h2>
        </div>
        <div className="form-group" style={{ maxWidth: 300 }}>
          <select value={activeQQ} onChange={(e) => setActiveQQ(e.target.value)}>
            {instances.map((i) => (
              <option key={i.qq} value={i.qq}>{i.qq}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="panel">
        <div className="settings-form">
          <div className="section">
            <div className="section-title">LLM 模型配置</div>
            <div className="form-group">
              <label>Base URL</label>
              <input
                value={cfg.llm.base_url}
                onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, base_url: e.target.value } })}
              />
            </div>
            <div className="form-group">
              <label>Model</label>
              <input
                value={cfg.llm.model}
                onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, model: e.target.value } })}
              />
            </div>
            <div className="form-group">
              <label>API Key</label>
              <input
                type="password"
                value={cfg.llm.api_key}
                onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, api_key: e.target.value } })}
              />
            </div>
            <button className="btn-ghost btn-sm" onClick={() => setCfg({ ...cfg, llm: { ...DEFAULT_LLM } })}>
              恢复默认
            </button>
          </div>

          <div className="section">
            <div className="section-title">上下文与记忆</div>
            <div className="form-row">
              <div className="form-group">
                <label>上下文最大 Token</label>
                <input
                  type="number"
                  value={cfg.context_max_tokens}
                  onChange={(e) => setCfg({ ...cfg, context_max_tokens: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>记忆检索条数</label>
                <input
                  type="number"
                  value={cfg.memory_retrieval_count}
                  onChange={(e) => setCfg({ ...cfg, memory_retrieval_count: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">回复行为</div>
            <div className="form-group">
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={cfg.reply_split_enabled}
                  onChange={(e) => setCfg({ ...cfg, reply_split_enabled: e.target.checked })}
                  style={{ width: "auto" }}
                />
                长回复自动拆分发送
              </label>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>最多拆分条数</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={cfg.reply_split_max}
                  onChange={(e) => setCfg({ ...cfg, reply_split_max: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>私聊合并等待 (秒)</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  step={0.5}
                  value={cfg.private_merge_delay}
                  onChange={(e) => setCfg({ ...cfg, private_merge_delay: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>群聊合并等待 (秒)</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  step={0.5}
                  value={cfg.group_merge_delay}
                  onChange={(e) => setCfg({ ...cfg, group_merge_delay: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">功能开关</div>
            <div className="form-group">
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={cfg.web_search_enabled}
                  onChange={(e) => setCfg({ ...cfg, web_search_enabled: e.target.checked })}
                  style={{ width: "auto" }}
                />
                启用网络搜索（必应）
              </label>
            </div>
          </div>

          <div className="section">
            <div className="section-title">连接端口</div>
            <div className="form-row">
              <div className="form-group">
                <label>OneBot WebSocket 端口</label>
                <input
                  type="number"
                  value={cfg.onebot_ws_port}
                  onChange={(e) => setCfg({ ...cfg, onebot_ws_port: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>NapCat WebUI 端口</label>
                <input
                  type="number"
                  value={cfg.napcat_webui_port}
                  onChange={(e) => setCfg({ ...cfg, napcat_webui_port: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">管理员 / 特殊角色</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(cfg.admins || []).map((a: Record<string, string>, i: number) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    placeholder="QQ号"
                    value={a.qq || ""}
                    style={{ width: 160 }}
                    onChange={(e) => {
                      const list = [...(cfg.admins || [])];
                      list[i] = { ...list[i], qq: e.target.value };
                      setCfg({ ...cfg, admins: list });
                    }}
                  />
                  <input
                    placeholder="角色（如：妈妈）"
                    value={a.role || ""}
                    style={{ flex: 1 }}
                    onChange={(e) => {
                      const list = [...(cfg.admins || [])];
                      list[i] = { ...list[i], role: e.target.value };
                      setCfg({ ...cfg, admins: list });
                    }}
                  />
                  <button className="btn-danger btn-sm" onClick={() => {
                    setCfg({ ...cfg, admins: (cfg.admins || []).filter((_: any, j: number) => j !== i) });
                  }}>删除</button>
                </div>
              ))}
              <button className="btn-ghost btn-sm" onClick={() => {
                setCfg({ ...cfg, admins: [...(cfg.admins || []), { qq: "", role: "" }] });
              }}>+ 添加</button>
            </div>
          </div>

          <button className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存设置"}
          </button>
        </div>
      </div>
    </div>
  );
}
