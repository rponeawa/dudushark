# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

嘟嘟鲨鱼 (DuduShark) — 基于 NapCatQQ 的 QQ 机器人，使用 OneBot v11 反向 WebSocket 协议。后端 Python/FastAPI，前端 React/Vite/TypeScript，向量记忆用 ChromaDB + SiliconFlow BAAI/bge-m3 嵌入 API。

目标平台：Linux。NapCatQQ 通过 Docker 运行，dudushark 直接运行在宿主机上。

## 常用命令

```bash
# 完整启动（创建 venv、安装依赖、构建前端、启动 NapCat Docker 容器、启动服务）
./start.sh

# 仅后端（跳过前端构建，需要先构建过前端）
.venv/bin/python -m server.main [host] [port]

# 前端开发（带热重载，API 代理到 8080）
cd web && npm run dev

# 前端构建
cd web && npm run build

# Python 依赖安装（Python 3.14 需加 --only-binary :all:）
.venv/bin/pip install --only-binary :all: -r requirements.txt

# 运行测试
PYTHONPATH=. .venv/bin/python tests/test_memory.py
PYTHONPATH=. .venv/bin/python tests/test_memory_natural.py
PYTHONPATH=. .venv/bin/python tests/test_merge_group.py
PYTHONPATH=. .venv/bin/python tests/test_reminders.py
```

## 环境变量

`.env.example` 中有完整模板。`start.sh` 自动加载 `.env`。

| 变量 | 说明 |
|------|------|
| `STEPFUN_API_KEY` | 阶跃星辰 LLM API Key |
| `SILICONFLOW_API_KEY` | SiliconFlow 嵌入 API Key (BAAI/bge-m3) |
| `WEBUI_PASSWORD` | WebUI 面板登录密码（不设置则跳过鉴权） |
| `DUDUSHARK_DATA` | 数据目录，默认 `./data` |

## 架构

```
NapCatQQ (Docker: mlikiowa/napcat-docker)
  └─ WS → ws://172.17.0.1:8080/onebot/v11/ws/{qq}   (OneBot v11 反向WS)
            └─ server/main.py                          (FastAPI + WS 端点)
                 ├─ bot/onebot_handler.py              (OneBot 协议解析，create_task 异步分发)
                 ├─ bot/message_handler.py             (消息合并缓冲 → LLM 调用 → 回复拆分)
                 ├─ bot/persona.py                     (System prompt 人设定义)
                 ├─ bot/mood.py                         (全局心情/睡眠：影响所有回复+主动发言)
                 ├─ memory/manager.py                  (按 user_id 分目录的 MD 记忆 CRUD)
                 ├─ memory/vector_store.py             (ChromaDB + SiliconFlow embedding)
                 ├─ memory/context.py                  (128K token 上下文压缩，摘要合并)
                 ├─ search/bing.py                     (Bing/DDG HTML 解析搜索)
                 ├─ bot/proactive.py                   (主动消息调度：心情/睡眠/好奇心驱动)
                 ├─ napcat/manager.py                  (NapCatQQ 配置生成，WebUI API 交互)
                 └─ webui/routes.py                    (REST API + WS 事件推送)
```

## 核心数据流

**消息处理链路：**
1. NapCatQQ 通过反向 WS 发送 OneBot 事件 → `onebot_handler._dispatch()`
2. 消息类型事件用 `create_task` 调度到后台，不阻塞 WS 接收循环
3. `message_handler.handle()` 使用 Future 机制：群聊/私聊均缓冲合并同用户连续消息，新消息重置计时器
4. 合并窗口到期后，调用 LLM 生成回复，`[SKIP]` 表示不回复
5. LLM 返回 JSON：`{"reply":"...","quote":bool,"memory":null|{...},"diary":null|{...},"group_memory":null|{...},"forget":null|{...}}`
6. 若 JSON 含 `say`+`search` 字段：先发 `say` 消息，后台搜索 → 二次 LLM → 发最终回复（真实异步多步）
7. 长回复按 `。！？\n～` 自然断句拆分发送，间隔按字数模拟打字（max(2.0, len*0.08+1.0)）
8. `[SKIP]` 不回复，LLM 最终调用失败返回 `[]` 不回复
9. LLM 调用带有指数退避重试（3次, 2/4/8s），全局速率限制（滑动窗口 8次/60s）

**消息合并：**
- `handle()` 返回 `list[ReplyPart]`，每个包含 `text` 和可选 `quote_msg_id`
- 合并窗口内多个 caller 通过共享 Future 等待同一结果，仅第一个 caller 获得回复文本，其余收到 `[]` 避免重复发送
- 默认私聊合并等 8s、群聊 10s（群聊所有说话人合并到同一窗口）
- 私聊最大窗口 20s、群聊 60s
- 合并后格式为 `[N] name: text`，LLM 通过序号区分说话人
- 群聊中 `names_map` 追踪每个名字对应的 user_id，LLM 的 memory 可带 `user` 字段指定归属

**记忆系统：**
- `data/instances/{bot_qq}/memories/{user_id}/` — 按用户的 MD 文件
- `data/instances/{bot_qq}/chroma/` — ChromaDB 持久化，每个 user_id 一个 collection
- 同一 user_id 的私聊和群聊记忆共享，存储在同一个目录和 ChromaDB collection
- LLM 通过 JSON `memory`/`diary`/`group_memory`/`forget` 字段自主管理记忆增删改
- memory 的 `user` 字段 + `names_map` 确保群聊中记忆归属正确用户
- `__diary__` — 鱼的全局记忆，`__group__<id>` — 群聊记忆
- 相同 category+title → upsert 更新（看法可随时间改变），不同 → 新建
- 嵌入失败时返回零向量（非随机向量），并记录 warning 日志
- ChromaDB collection 名称使用 `strip("_")` 清理，避免 `__diary__` 等非法名称

**对话持久化：**
- 对话历史落盘到 `data/instances/{qq}/conversations/{key}.jsonl`
- 每次 `_append_history` 后自动写入文件
- 启动时从文件恢复所有对话
- 无条数上限

**上下文压缩：**
- `context.fit_messages()` 从末尾向前填充消息，超出预算的压缩为摘要
- 多次压缩的摘要自动合并（`_coalesce_summaries`），确保始终 ≤1 条摘要消息
- 摘要本身计入 token 预算，会为摘要腾出空间
- 群聊所有用户共享同一份对话历史（key=group_id），私聊各自独立

**全局心情系统：**
- `mood.py` 中的 `DuduMood` 是每个 QQ 实例的单例，被 proactive scheduler 和 message handler 共享
- 小时心情曲线作为基线，Dudu 可以随机偏离 ±0.15，每 2-6 小时重新决定
- 特殊状态：`night_owl`（深夜抗拒睡意 25% 概率）、`daydream`（白天莫名犯困 12% 概率）
- `system_mood_context()` 生成心情描述注入系统 prompt，让 LLM 知晓当前状态
- `llm_temperature()` / `llm_max_tokens()` 根据睡眠状态调整参数（困时温度 0.75、刚醒 0.90）
- 前端状态面板实时显示睡眠状态 + 精力条

**主动消息：**
- `proactive.py` 中的 `ProactiveScheduler` 读取全局 `DuduMood`，不再拥有独立的心情/睡眠状态
- 仅在她曾有回复的对话中主动发言
- 动态唤醒间隔：根据嘟嘟是否在活跃聊天自动调整
  - 最近有回复（engaged）：3-8 分钟
  - 有人说话但她没参与（idle）：15-45 分钟
  - 完全安静（quiet）：30-60 分钟
- 睡眠状态再叠加系数（困时 x2.5，刚醒/夜猫子 x0.5）

**Prompt 缓存优化：**
- LLM 消息构建为独立 system 消息：[0]=固定人设(始终命中缓存) [1]=心情 [2]=日记 [3]=群记忆 [4]=个人记忆 [5+]=历史
- `msg[0]` 永远不变 → prefix cache 命中率 100%
- 记忆日期格式化为易读的 `MM-DD HH:MM` 而非 ISO 8601

**定时提醒系统：**
- LLM 通过 JSON `remind` 字段创建一次性定时任务：`{"at_utc": Unix时间戳, "content": "提醒内容"}`
- 存储到 `data/instances/{qq}/reminders.json`，调度器每周期检查
- 到点通过 OneBot 发送消息，发送后自动删除，不重复

**隐私保护：**
- `admins_description` 仅在发送者本人是管理员时注入 system prompt
- 家族记忆（`family_memory` + `family_note`）仅对 role 含特定标识的成员在私聊中注入
- 群聊中不注入个人记忆和家族记忆，日记内容禁止透露具体人名
- Persona 含隐私铁律：绝对不泄露他人记忆、私聊内容或个人信息
- 记忆日期格式化为易读的 `MM-DD HH:MM` 而非 ISO 8601

**JSON 格式指令：**
- memory: `{"user":"名字","category":"类别","title":"标题","content":"内容"}` — user 字段指定归属
- group_memory: `{"category":"类别","title":"标题","content":"内容"}` — 群整体信息（仅群聊）
- diary: 同 memory 格式，值得写才写
- forget: `{"category":"类别","title":"标题"}` — 删除记忆
- remind: `{"at_utc": Unix时间戳, "content": "提醒内容"}` — 一次性定时提醒
- say+search: `{"say":"...","search":"..."}` — 多步搜索

**角色/管理员系统：**
- `BotConfig.admins` 列表，通过 `admins_description` 注入 system prompt 让鱼识别特殊身份
- 运行时根据 QQ 号匹配，用户名后标注【角色】标签，无法伪造
- 管理员描述和家族记忆仅对本人私聊时注入，防止信息泄露
- 前端 Settings 页面可管理

**其他关键规则：**
- 自称"鱼"（不是"我"或"咱"）
- 冒犯内容 → 立刻变脸厌恶，称呼"讨厌的人类"，记入记忆
- 记忆可随时间更新：以前讨厌的人改过自新后用同一 category+title 覆盖
- 搜索必须用鱼的语气转述，不能直接粘贴结果

**NapCatQQ (Docker)：**
- 使用 `mlikiowa/napcat-docker:latest` 镜像
- 端口 6099 映射到宿主机
- 配置目录 `~/NapCatQQ/config/` 挂载到容器 `/app/napcat/config/`
- OneBot 反向 WS 连接 `ws://172.17.0.1:8080/onebot/v11/ws/{qq}`
- `start.sh` 自动检测并启动容器

## 多实例隔离

每个 QQ 号拥有独立的数据目录和 NapCatQQ 配置。扫描不同 QQ 号登录时创建全新实例，配置和记忆完全隔离。

```
data/instances/{qq}/
  ├── bot_config.json
  ├── memories/{user_id}/*.md
  ├── chroma/
  ├── conversations/{key}.jsonl
  └── napcat_instances/{qq}/config/   (NapCatQQ 配置文件)
```

## API 路由

所有 API 前缀 `/api`，WebSocket `/api/ws/widget` 用于前端实时事件推送。除 `/api/auth/login` 和 `/api/ws/widget` 外均需 Bearer token 鉴权。

关键端点：`/auth/login`（登录获取 token）、`/status`（系统健康 + LLM 检查 60s 缓存）、`/instances`（CRUD）、`/instances/{qq}/config`（模型/行为配置）、`/instances/{qq}/memories/{user_id}`（记忆管理）、`/instances/{qq}/conversations/{key}`（对话历史）。
