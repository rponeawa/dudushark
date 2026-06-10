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
    return <div className="empty-state">请先在「实例」页面创建并启动一个实例</div>;
  }

  if (!cfg) {
    return <div className="empty-state">加载中...</div>;
  }

  return (
    <div>
      {msg && <div className={`toast show ${msg.includes("失败") ? "err" : "ok"}`}>{msg}</div>}

      <div className="panel">
        <div className="panel-header">
          <h2>实例选择</h2>
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
            <div className="section-title">LLM 模型</div>
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
            <div className="form-row">
              <div className="form-group">
                <label>私聊合并等待 (秒)</label>
                <input
                  type="number" min={1} max={10} step={0.5}
                  value={cfg.private_merge_delay}
                  onChange={(e) => setCfg({ ...cfg, private_merge_delay: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>群聊合并等待 (秒)</label>
                <input
                  type="number" min={1} max={10} step={0.5}
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
                网络搜索
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={cfg.tts_enabled !== false}
                  onChange={(e) => setCfg({ ...cfg, tts_enabled: e.target.checked })}
                  style={{ width: "auto" }}
                />
                语音发送 (TTS)
              </label>
            </div>
          </div>

          <div className="section">
            <div className="section-title">语音合成 (TTS)</div>
            <div className="form-row">
              <div className="form-group">
                <label>TTS 模型</label>
                <input value={cfg.tts_model || "step-tts-2"} onChange={(e) => setCfg({ ...cfg, tts_model: e.target.value })} />
              </div>
              <div className="form-group">
                <label>音色 Voice ID</label>
                <input value={cfg.tts_voice || "ruanmengnvsheng"} onChange={(e) => setCfg({ ...cfg, tts_voice: e.target.value })} />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">语音转文字 (ASR)</div>
            <div className="form-group">
              <label>ASR 模型</label>
              <input value={cfg.asr_model || "step-audio-2"} onChange={(e) => setCfg({ ...cfg, asr_model: e.target.value })} />
            </div>
            <div className="form-group">
              <label>转写提示词</label>
              <textarea
                value={cfg.asr_prompt || ""}
                onChange={(e) => setCfg({ ...cfg, asr_prompt: e.target.value })}
                rows={3}
                placeholder="语音转文字的提示词"
              />
            </div>
          </div>

          <div className="section">
            <div className="section-title">端口</div>
            <div className="form-row">
              <div className="form-group">
                <label>OneBot WebSocket</label>
                <input
                  type="number"
                  value={cfg.onebot_ws_port}
                  onChange={(e) => setCfg({ ...cfg, onebot_ws_port: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>NapCat WebUI</label>
                <input
                  type="number"
                  value={cfg.napcat_webui_port}
                  onChange={(e) => setCfg({ ...cfg, napcat_webui_port: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">家族记忆 (family_memory)</div>
            <div className="form-group">
              <textarea
                value={cfg.family_memory || ""}
                onChange={(e) => setCfg({ ...cfg, family_memory: e.target.value })}
                rows={6}
                placeholder="仅家人私聊时注入的家族记忆"
              />
            </div>
            <div className="form-group">
              <label>家族提醒 (family_note)</label>
              <textarea
                value={cfg.family_note || ""}
                onChange={(e) => setCfg({ ...cfg, family_note: e.target.value })}
                rows={3}
                placeholder="伴随家族记忆的提示词"
              />
            </div>
          </div>

          <div className="section">
            <div className="section-title">管理员描述 (admins_description)</div>
            <div className="form-group">
              <textarea
                value={cfg.admins_description || ""}
                onChange={(e) => setCfg({ ...cfg, admins_description: e.target.value })}
                rows={4}
                placeholder="注入 system prompt 的管理员描述"
              />
            </div>
          </div>

          <div className="section">
            <div className="section-title">管理员</div>
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
                    placeholder="角色"
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
              }}>添加</button>
            </div>
          </div>

          <div className="section">
            <h3>数据备份</h3>
            <div className="row" style={{ gap: 12 }}>
              <button className="btn-ghost" onClick={async () => {
                const token = localStorage.getItem("token") || "";
                try {
                  const r = await fetch(`/api/instances/${activeQQ}/backup`, {
                    headers: { "Authorization": `Bearer ${token}` },
                  });
                  if (!r.ok) { toast("导出失败", false); return; }
                  const blob = await r.blob();
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url; a.download = `dudushark-backup-${activeQQ}.zip`;
                  a.click(); URL.revokeObjectURL(url);
                  toast("导出成功");
                } catch { toast("导出失败", false); }
              }}>导出备份 (.zip)</button>
              <label className="btn-ghost" style={{ cursor: "pointer", position: "relative" }}>
                导入恢复
                <input type="file" accept=".zip" style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer" }}
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    if (!confirm("导入将合并聊天和记忆、覆盖配置，并重启服务。确定继续？")) return;
                    const token = localStorage.getItem("token") || "";
                    const form = new FormData();
                    form.append("backup_file", file);
                    try {
                      const r = await fetch(`/api/instances/${activeQQ}/backup/restore`, {
                        method: "POST",
                        headers: { "Authorization": `Bearer ${token}` },
                        body: form,
                      });
                      const d = await r.json();
                      if (r.ok) toast(d.message || "恢复成功，服务重启中");
                      else toast(d.detail || "恢复失败", false);
                    } catch { toast("恢复失败", false); }
                  }}
                />
              </label>
            </div>
          </div>

          <button className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
