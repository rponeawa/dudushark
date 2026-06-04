# 嘟嘟鲨鱼 DuduShark

一只来自鲨鱼星的赛博大鲨鱼 QQ 机器人，基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) + OneBot v11 反向 WebSocket 协议。

**后端**: Python/FastAPI · **前端**: React/Vite/TypeScript · **向量记忆**: ChromaDB + SiliconFlow BAAI/bge-m3

## 特性

- **角色人格** — 傲娇、善良、喜欢睡觉和软绵绵的东西，口头禅"啊呜～"
- **多实例隔离** — 每个 QQ 号独立的数据目录、配置和 NapCatQQ 进程
- **向量记忆** — ChromaDB 持久化，BAAI/bge-m3 嵌入，按用户检索长期记忆
- **128K 上下文** — 超出时自动压缩旧消息为摘要，摘要自动合并避免堆积
- **消息合并** — 检测同一用户短时间内的连续消息，合并后一次回复
- **引用回复** — 自动选择合适的消息进行引用回复
- **长回复拆分** — 按句末标点自然断句，分段发送
- **网络搜索** — Bing/DDG HTML 解析，不依赖搜索 API
- **群聊安静** — 自由判断是否参与话题，不过度发言
- **全局心情系统** — 小时曲线 × 睡眠节律 × 个性偏离，影响回复风格、温度、主动发言
- **主动消息** — 基于全局心情自主发起对话，仅限曾聊过的人/群，LLM 可决定不说话
- **Web 管理面板** — 深海主题 UI，实例管理、记忆浏览、对话查看、配置修改，实时心情状态
- **QR 码登录** — 通过 WebUI 扫码登录 QQ
- **一键安装** — `start.sh` 自动下载安装 NapCatQQ、签名原生模块、桥接 macOS QQ 路径

## 前置条件

- Python 3.10+（3.14 需 `--only-binary :all:`）
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

# 3. 一键启动（自动创建 venv、安装依赖、构建前端）
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
| `SILICONFLOW_API_KEY` | 是 | SiliconFlow 嵌入模型 API Key |
| `DUDUSHARK_DATA` | 否 | 数据目录，默认 `./data` |

启动脚本 `start.sh` 会自动加载 `.env` 文件。

## 架构

```
NapCatQQ (QQ客户端)
  └─ WS → ws://host:8080/onebot/v11/ws/{qq}   (OneBot v11 反向WS)
            └─ server/main.py                  (FastAPI + WS 端点)
                 ├─ bot/onebot_handler.py      (OneBot 协议解析)
                 ├─ bot/message_handler.py     (消息合并缓冲 → LLM → 回复拆分)
                 ├─ bot/persona.py             (System prompt 人设)
                 ├─ memory/manager.py          (MD 记忆 CRUD)
                 ├─ memory/vector_store.py     (ChromaDB + embedding)
                 ├─ memory/context.py          (128K 上下文压缩)
                 ├─ search/bing.py             (Bing/DDG HTML 搜索)
                 ├─ bot/mood.py                 (全局心情/睡眠系统)
                 ├─ bot/proactive.py           (主动消息调度)
                 ├─ napcat/manager.py          (NapCatQQ v4.x 进程管理)
                 └─ webui/routes.py            (REST API + WS 事件推送)
```

### 消息处理链路

1. NapCatQQ 通过反向 WS 发送 OneBot 事件 → `onebot_handler._dispatch()`
2. 消息事件用 `create_task` 异步调度，不阻塞 WS 接收循环
3. `message_handler.handle()` 使用 Future 机制合并同用户连续消息
4. 合并窗口到期后调用 LLM 生成回复，`[SKIP]` 表示不回复
5. 回复 `>>` 前缀表示引用回复，转为 OneBot reply segment
6. 长回复按 `。！？\n` 自然断句拆分发送

### 全局心情系统

嘟嘟拥有一个全局的心情和睡眠系统，影响她所有的行为——不仅仅是主动发言，也包括回复消息时的语气、温度和话多少。

- **小时心情基线**：凌晨安静 → 晚上最活跃，但嘟嘟可以**自己决定**偏离基线（夜猫子模式、白日梦模式）
- **睡眠状态机**：awake → sleepy → just_woke 三态随机切换，外加夜间抗拒睡意（夜猫子）、白天莫名犯困（白日梦）
- **影响范围**：
  - 回复消息的 LLM 温度（困时 0.75，刚醒 0.90）、最大 token 数
  - 系统 prompt 注入当前心情描述，让 LLM 知晓自己的状态
  - 主动发言的概率
- **前端可见**：主面板实时显示睡眠状态 + 精力条

### 主动消息

嘟嘟基于全局心情系统，在曾被动回复过的私聊或群聊中偶尔主动开口。LLM 可用 `[SKIP]` 决定不说话。全局冷却 10 分钟，单对话冷却 45 分钟。

### 记忆系统

- `data/instances/{qq}/memories/{user_id}/` — 按用户的 MD 文件
- `data/instances/{qq}/chroma/` — ChromaDB 持久化，每个 user_id 一个 collection
- 同一用户的私聊和群聊记忆共享
- 嵌入失败时返回零向量，不产生虚假相似度

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
│   │   ├── persona.py      # 角色人设
│   │   ├── mood.py         # 全局心情/睡眠
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
│       ├── App.tsx         # SPA 路由
│       ├── api.ts          # API 客户端
│       └── pages/          # 页面组件
│           ├── Status.tsx
│           ├── Instances.tsx
│           ├── Conversations.tsx
│           ├── Memories.tsx
│           └── Settings.tsx
├── start.sh                # 一键启动脚本
├── requirements.txt
└── CLAUDE.md               # Claude Code 指引
```
