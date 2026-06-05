# 嘟嘟鲨鱼 DuduShark

一只来自鲨鱼星的赛博大鲨鱼 QQ 机器人，基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) + OneBot v11 反向 WebSocket 协议。

**后端**: Python/FastAPI  **前端**: React/Vite/TypeScript  **向量记忆**: ChromaDB + SiliconFlow BAAI/bge-m3

## 特性

- **角色人格** — 傲娇、善良、喜欢睡觉，自称"鱼"，口头禅"啊呜～"。遇冒犯会变脸
- **多实例隔离** — 每个 QQ 号独立的数据目录、配置和 NapCatQQ 进程
- **向量记忆** — ChromaDB + BAAI/bge-m3，LLM 自主增删改。支持全局记忆、群聊记忆
- **多步执行** — 先说一句表示去查，再搜索，最后用自己的话转述结果
- **JSON 格式** — 一次 LLM 调用输出 reply + quote + memory + diary + group_memory
- **128K 上下文** — 超出自动压缩，prompt cache 命中率 100%（固定人设前缀）
- **消息合并** — 同用户连续消息合并后一次回复，群聊多人共享合并窗口
- **网络搜索** — Bing/DDG HTML 解析，LLM 自主触发
- **群聊安静** — SKIP 为主，只在 @鱼 / 戳一戳 / 真正感兴趣时开口
- **全局心情系统** — 小时曲线 x 睡眠节律 x 个性偏离，影响回复风格
- **主动消息** — 基于心情自主发起，动态唤醒间隔按活跃度调节，睡眠时段免打扰
- **定时提醒** — 一次性定时任务，LLM 自主计算时间戳，到点发送后自动删除
- **Web 管理面板** — 密码鉴权，侧边栏布局，记忆管理（个人/群聊/全局记忆），对话查看
- **隐私保护** — 角色标签仅对本人注入，冒充者无法获取；群聊不暴露他人记忆
- **速率保护** — 滑动窗口 8次/60s，指数退避重试

## 前置条件

- Linux（仅支持 Linux）
- Python 3.10+
- Node.js 18+
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（QQ 机器人框架）
- SiliconFlow API Key（向量嵌入，[注册](https://siliconflow.cn)）
- LLM API（阶跃星辰 StepFun 或其他兼容 OpenAI 格式的 API）

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/rponeawa/dudushark.git
cd dudushark

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 3. 一键启动（自动安装 NapCatQQ、创建 venv、安装依赖、构建前端）
./start.sh

# 4. 打开 WebUI
# http://127.0.0.1:8080
```

### 手动启动

```bash
# Python 依赖
python3 -m venv .venv
source .venv/bin/activate
pip install --only-binary :all: -r requirements.txt   # Python 3.14 需要 --only-binary

# 前端构建
cd web && npm install && npm run build && cd ..

# 启动服务
.venv/bin/python -m server.main [host] [port]

# 前端开发模式（热重载，API 代理到 8080）
cd web && npm run dev
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `STEPFUN_API_KEY` | 是 | 阶跃星辰 LLM API Key |
| `SILICONFLOW_API_KEY` | 是 | SiliconFlow 嵌入 API Key |
| `WEBUI_PASSWORD` | 建议 | WebUI 面板登录密码，不设置则跳过鉴权 |
| `DUDUSHARK_DATA` | 否 | 数据目录，默认 `./data` |

启动脚本 `start.sh` 会自动加载 `.env` 文件。

## 架构

```
NapCatQQ (QQ客户端)
  └─ WS  →  ws://host:8080/onebot/v11/ws/{qq}   (OneBot v11 反向WS)
              └─ server/main.py                   (FastAPI + WS 端点)
                   ├─ bot/onebot_handler.py       (OneBot 协议解析)
                   ├─ bot/message_handler.py      (消息合并缓冲 → LLM → 回复拆分)
                   ├─ bot/persona.py              (System prompt 人设)
                   ├─ bot/mood.py                  (全局心情/睡眠系统)
                   ├─ memory/manager.py           (MD 记忆 CRUD)
                   ├─ memory/vector_store.py      (ChromaDB + SiliconFlow embedding)
                   ├─ memory/context.py           (128K 上下文压缩)
                   ├─ search/bing.py              (Bing/DDG HTML 搜索)
                   ├─ bot/proactive.py            (主动消息调度)
                   ├─ napcat/manager.py           (NapCatQQ 进程管理)
                   └─ webui/routes.py             (REST API + WS 事件推送)
```

### 消息处理链路

1. NapCatQQ 通过反向 WS 发送 OneBot 事件 → `onebot_handler._dispatch()`
2. 消息事件用 `create_task` 异步调度，不阻塞 WS 接收循环
3. `message_handler.handle()` 使用 Future 机制合并同用户连续消息
4. 合并窗口到期后调用 LLM 生成回复，`[SKIP]` 表示不回复
5. LLM 返回 JSON：`{"reply":"...","quote":false,"memory":null,"diary":null,"group_memory":null,"forget":null}`
6. memory 带 `user` 字段指定归属人，群聊中自动映射到正确 user_id
7. 若 JSON 含 `say`+`search`：先发 `say`，后台搜索 → 二次 LLM → 发最终回复
8. 长回复按 `。！？\n～` 断句拆分发送，间隔 `max(2.0, len*0.08+1.0)`

### 群聊行为

嘟嘟在群里 SKIP 为主，只在以下情况开口：有人 @鱼、有人戳一戳、真的对话题感兴趣。群聊合并窗口 10s，多说话人消息合并为 `[N] name: text` 格式，LLM 可指定记忆归属。

### 全局心情系统

小时心情曲线 x 睡眠节律（10% 概率犯困，清醒 1-2 小时）x 夜猫子/白日梦特殊状态。影响回复温度、token 数、主动发言概率。

### 记忆系统

- `data/instances/{qq}/memories/{user_id}/` — 按用户的 MD 文件
- `data/instances/{qq}/chroma/` — ChromaDB 持久化，每个 user_id 一个 collection
- 同一用户的私聊和群聊记忆共享
- LLM 通过 JSON `memory`/`diary`/`group_memory`/`forget` 管理增删改
- `memory` 的 `user` 字段指定归属人，`names_map` 映射到正确 user_id
- 相同 category+title 自动 upsert 更新
- `__diary__` — 全局记忆，`__group__<id>` — 群聊记忆
- 嵌入失败时返回零向量，并记录 warning 日志

### 对话持久化

- 对话历史落盘到 `data/instances/{qq}/conversations/{key}.jsonl`
- 启动时自动恢复，无条数上限

### 定时提醒

- LLM 通过 `remind` JSON 字段创建一次性定时任务
- ProactiveScheduler 每周期检查，到点自动发送后删除

### 管理员代传话

- 管理员私聊中可通过 `relay` JSON 字段代传消息给另一位管理员
- 仅明确"帮我告诉/转达/跟XX说"时触发，非管理员和群聊不可用

### 隐私保护

- 管理员描述仅在发送者本人匹配时注入，冒充无法获取角色标签
- 家族记忆仅对特定成员在私聊中注入，群聊不可见
- 群聊不注入个人记忆和全局记忆内容

## WebUI API

所有 API 前缀 `/api`，WebSocket `/api/ws/widget` 用于前端实时事件推送。

关键端点：
- `GET /api/status` — 系统健康 + LLM 检查（60s 缓存）
- `GET/POST /api/instances` — 实例 CRUD
- `GET/PUT /api/instances/{qq}/config` — 模型/行为配置
- `GET/DELETE /api/instances/{qq}/conversations/{key}` — 对话历史
- `GET/POST/DELETE /api/instances/{qq}/memories/{user_id}` — 记忆管理

## 项目结构

```
dudushark/
├── server/                 # Python 后端
│   ├── main.py             # FastAPI 入口
│   ├── config.py           # 配置模型
│   ├── bot/                # 机器人核心
│   │   ├── onebot_handler.py
│   │   ├── message_handler.py
│   │   ├── mood.py         # 全局心情/睡眠
│   │   ├── persona.py      # 角色人设
│   │   └── proactive.py    # 主动消息调度
│   ├── memory/             # 记忆系统
│   │   ├── manager.py
│   │   ├── vector_store.py # ChromaDB
│   │   └── context.py      # 上下文压缩
│   ├── search/bing.py      # 网络搜索
│   ├── napcat/manager.py   # NapCatQQ 进程管理
│   └── webui/routes.py     # Web API
├── web/                    # React 前端
│   └── src/
│       ├── App.tsx         # SPA 路由（侧边栏）
│       ├── api.ts          # API 客户端
│       └── pages/          # 页面组件
├── tests/                  # 记忆系统测试脚本
├── start.sh                # 一键启动脚本
├── requirements.txt
├── CLAUDE.md
└── README.md
```

## 测试

```bash
PYTHONPATH=. .venv/bin/python tests/test_memory.py          # 记忆 CRUD 测试
PYTHONPATH=. .venv/bin/python tests/test_memory_natural.py  # 自然对话测试
PYTHONPATH=. .venv/bin/python tests/test_merge_group.py     # 群聊合并测试
PYTHONPATH=. .venv/bin/python tests/test_reminders.py       # 定时提醒测试
```
