# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

嘟嘟鲨鱼 (DuduShark) — 基于 NapCatQQ 的 QQ 机器人，使用 OneBot v11 反向 WebSocket 协议。后端 Python/FastAPI，前端 React/Vite/TypeScript，向量记忆用 ChromaDB + SiliconFlow BAAI/bge-m3 嵌入 API。

## 常用命令

```bash
# 完整启动（自动创建 venv、安装依赖、构建前端）
./start.sh

# 仅后端（跳过前端构建，需要先构建过前端）
.venv/bin/python -m server.main [host] [port]

# 前端开发（带热重载，API 代理到 8080）
cd web && npm run dev

# 前端构建
cd web && npm run build

# Python 依赖安装（Python 3.14 需加 --only-binary :all:）
.venv/bin/pip install --only-binary :all: -r requirements.txt
```

## 架构

```
NapCatQQ (QQ客户端)
  └─ WS → ws://host:8080/onebot/v11/ws/{qq}   (OneBot v11 反向WS)
            └─ server/main.py                  (FastAPI + WS 端点)
                 ├─ bot/onebot_handler.py      (OneBot 协议解析，create_task 异步分发)
                 ├─ bot/message_handler.py     (消息合并缓冲 → LLM 调用 → 回复拆分)
                 ├─ bot/persona.py             (System prompt 人设定义)
                 ├─ memory/manager.py          (按 user_id 分目录的 MD 记忆 CRUD)
                 ├─ memory/vector_store.py     (ChromaDB + SiliconFlow embedding)
                 ├─ memory/context.py          (128K token 上下文压缩，摘要合并)
                 ├─ search/bing.py             (Bing/DDG HTML 解析搜索)
                 ├─ napcat/manager.py          (NapCatQQ 进程生命周期 + 配置生成)
                 └─ webui/routes.py            (REST API + WS 事件推送)
```

## 核心数据流

**消息处理链路：**
1. NapCatQQ 通过反向 WS 发送 OneBot 事件 → `onebot_handler._dispatch()`
2. 消息类型事件用 `create_task` 调度到后台，不阻塞 WS 接收循环
3. `message_handler.handle()` 使用 Future 机制：群聊/私聊均缓冲合并同用户连续消息，新消息重置计时器
4. 合并窗口到期后，调用 LLM 生成回复，`[SKIP]` 表示不回复
5. 回复文本句首 `>>` 表示引用回复，转为 OneBot reply segment
6. 长回复按 `。！？\n` 自然断句拆分发送，间隔 1.5s

**消息合并：**
- `handle()` 返回 `list[ReplyPart]`，每个包含 `text` 和可选 `quote_msg_id`
- 合并窗口内多个 caller 通过共享 Future 等待同一结果，仅第一个 caller 获得回复文本，其余收到 `[]` 避免重复发送
- 默认私聊合并等 2s、群聊 3s，可通过 WebUI 设置调整

**记忆系统：**
- `data/instances/{bot_qq}/memories/{user_id}/` — 按用户的 MD 文件
- `data/instances/{bot_qq}/chroma/` — ChromaDB 持久化，每个 user_id 一个 collection
- 同一 user_id 的私聊和群聊记忆**共享**，存储在同一个目录和 ChromaDB collection
- 嵌入失败时返回零向量（非随机向量），并记录 warning 日志

**上下文压缩：**
- `context.fit_messages()` 从末尾向前填充消息，超出预算的压缩为摘要
- 多次压缩的摘要自动合并（`_coalesce_summaries`），确保始终 ≤1 条摘要消息
- 摘要本身计入 token 预算，会为摘要腾出空间

## 多实例隔离

每个 QQ 号拥有独立的数据目录和 NapCatQQ 进程。扫描不同 QQ 号登录时创建全新实例，配置和记忆完全隔离。

```
data/instances/{qq}/
  ├── bot_config.json
  ├── memories/{user_id}/*.md
  ├── chroma/
  └── napcat_instances/{qq}/config/   (NapCatQQ 配置文件)
```

## API 路由

所有 API 前缀 `/api`，WebSocket `/api/ws/widget` 用于前端实时事件推送。

关键端点：`/status`（系统健康 + LLM 检查 60s 缓存）、`/instances`（CRUD）、`/instances/{qq}/config`（模型/行为配置）、`/instances/{qq}/memories/{user_id}`（记忆管理）、`/instances/{qq}/conversations/{key}`（对话历史）。
