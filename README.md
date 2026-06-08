# 嘟嘟鲨鱼 DuduShark

一只来自鲨鱼星的赛博大鲨鱼 QQ 机器人，基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) + OneBot v11 反向 WebSocket 协议。

**后端**: Python/FastAPI  **前端**: React/Vite/TypeScript  **向量记忆**: ChromaDB + SiliconFlow BAAI/bge-m3  **LLM**: step-3.7-flash (多模态，支持函数调用)

## 特性

- **角色人格** — 傲娇、善良、喜欢睡觉，自称"鱼"，口头禅"啊呜～"。遇冒犯会变脸，拒绝调戏
- **多实例隔离** — 每个 QQ 号独立的数据目录、配置和 NapCatQQ 进程
- **向量记忆** — ChromaDB + BAAI/bge-m3，LLM 自主增删改。支持全局记忆、群聊记忆、家族记忆
- **多模态理解** — 支持图片、表情包、语音输入。语音自动转文字（ASR），表情包区分普通图片
- **语音发送** — LLM 自主决定发语音（撒娇/被要求时），支持情绪控制。StepFun TTS + WebUI 可配
- **网络搜索** — 函数调用式搜索，先说"啊呜～鱼去搜一下～"再异步搜索+二次 LLM 回复
- **JSON 格式** — 一次 LLM 调用输出 reply + quote + memory + diary + group_memory + remind + relay + qzone + search
- **128K 上下文** — 超出自动压缩为摘要，prompt cache 命中率优化，群聊私聊一致
- **消息合并** — 同用户连续消息合并后一次回复，群聊多人共享合并窗口
- **群聊静默** — SKIP 为主，独立 LLM 预判 + 二次验证，睡眠时段更严格
- **全局心情系统** — 小时曲线 x 睡眠节律，21点犯困 23点-8点睡着，前端实时显示
- **主动消息** — 精力×好奇驱动，相对亲密度评分，亲密度门槛挡住不熟的人。30分钟全局冷却，当日未联系免打扰
- **群聊暂停** — `/pause` `/resume` 管理员暂停/恢复群消息，不落盘
- **定时提醒** — 一次性定时任务，LLM 自主计算时间戳，私聊发送，前端可查看
- **QQ 空间发帖** — 管理员触发 + 每日自动发帖，主 LLM 写内容 + 独立 LLM 二次把关，WebUI tab
- **管理员代传话** — 延迟发送机制，WebUI tab 可查看/取消 pending，三层防护防误触
- **表情包收藏** — 自主收藏+本地落盘+向量搜索，独立通道发送，WebUI tab 可浏览/删除
- **情绪系统** — 单一情绪+百分比，平滑过渡，LLM 输出名字即可，WebUI 进度条显示
- **数据备份** — 一键导出/导入 zip（对话+记忆+配置+.env），WebUI 设置页操作
- **记忆质量控制** — 独立 LLM 判断是否值得记录，防琐碎信息泛滥
- **语音测试命令** — `/say [情绪] 文本` 管理员快速测试 TTS 语音
- **Web 管理面板** — 密码鉴权，侧边栏+顶栏，记忆管理，对话查看，定时提醒显示
- **隐私保护** — 管理描述和家族记忆仅私聊注入，群聊不暴露
- **速率保护** — 滑动窗口 8次/60s，指数退避重试

## 前置条件

- Linux（仅支持 Linux）
- Python 3.10+
- Node.js 18+
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（QQ 机器人框架，推荐 Docker）
- SiliconFlow API Key（向量嵌入，[注册](https://siliconflow.cn)）
- StepFun API Key（LLM，[注册](https://platform.stepfun.ai)）

## 快速开始

```bash
git clone https://github.com/rponeawa/dudushark.git
cd dudushark
cp .env.example .env   # 编辑填入 API Key
./start.sh             # 一键启动
# WebUI: http://127.0.0.1:8080
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `STEPFUN_API_KEY` | 是 | 阶跃星辰 LLM API Key |
| `SILICONFLOW_API_KEY` | 是 | SiliconFlow 嵌入 API Key |
| `WEBUI_PASSWORD` | 建议 | WebUI 面板登录密码 |
| `DUDUSHARK_DATA` | 否 | 数据目录，默认 `./data` |

## 架构

```
NapCatQQ (Docker)
  └─ WS → ws://host:8080/onebot/v11/ws/{qq}
            └─ server/main.py (FastAPI)
                 ├─ bot/onebot_handler.py    (OneBot 协议 + 图片提取)
                 ├─ bot/message_handler.py   (合并缓冲 → LLM → 拆分)
                 ├─ bot/persona.py           (人设)
                 ├─ bot/mood.py               (心情/睡眠)
                 ├─ bot/proactive.py         (主动消息 + 提醒调度 + QQ空间自动发帖)
                 ├─ qzone.py                 (QQ 空间说说 API)
                 ├─ memory/manager.py        (MD 记忆 CRUD)
                 ├─ memory/vector_store.py   (ChromaDB + embedding)
                 ├─ memory/context.py        (上下文压缩)
                 ├─ search/bing.py           (Bing/DDG 搜索)
                 ├─ napcat/manager.py        (NapCatQQ 管理)
                 └─ webui/routes.py          (REST API + WS)
```

### 消息处理链路

1. NapCatQQ WS 发送 OneBot 事件 → `onebot_handler` 解析（含图片 URL 提取）
2. 消息合并缓冲（私聊 8s / 群聊 9s），Future 机制共享结果
3. LLM 生成 JSON 回复，`[SKIP]` 不回复
4. 长回复不限段数，按 `。！？\n～` 断句，间隔模拟打字
5. 图片消息以多模态格式传给 LLM（`image_url` content 数组）

### 群聊行为

SKIP 三层防护：
- 主 LLM 自己判断是否回复（含 @鱼 / 戳一戳）
- 主 LLM 决定回复后，独立 LLM 二次验证（仅看最近 10 分钟上下文）
- 睡眠时段追加"正在睡觉"提示，SKIP 概率大幅提高

### 心情系统

- 21:00-23:00 犯困，23:00-08:00 睡着（energy 5-8%），前端显示"困了"/"睡着了"
- 08:00-21:00 清醒，10% 概率随机犯困，醒来后倍增
- 影响回复温度、token 数、主动发言概率

### 记忆系统

- 每人独立 ChromaDB collection，向量检索隔离
- memory/diary/forget 由独立 LLM 二次判断是否值得记录
- 群聊合并消息时检索所有说话人的记忆
- 已有标题列表展示给 LLM，便于 upsert 更新而非新建
- `__diary__` 全局记忆，`__group__<id>` 群聊记忆

### QQ 空间发帖

- **管理员触发**：管理员消息含"空间/说说/动态"关键词时，注入 qzone JSON 字段。主 LLM 自行判断是否写内容，独立 LLM 二次把关后才发帖
- **每日自动发帖**：清醒时段（8-21点）10% 概率触发，基于当天 diary 记忆生成内容
- WebUI 可手动触发发帖并查看历史

### 定时提醒

- `remind` JSON 字段创建，ProactiveScheduler 每周期检查
- 始终私聊发送，前端状态页可查看
- 有提醒时不创建记忆，避免重复

### 管理员代传话

- 仅管理员私聊可用，role 含"妈"为家族成员
- 三层防护：主 LLM 判断 + 独立 LLM 验证（无上文）+ 30s 去重
- 家族记忆仅私聊注入，群聊完全阻止

## 测试

```bash
PYTHONPATH=. .venv/bin/python tests/test_memory.py
PYTHONPATH=. .venv/bin/python tests/test_memory_natural.py
PYTHONPATH=. .venv/bin/python tests/test_merge_group.py
PYTHONPATH=. .venv/bin/python tests/test_reminders.py
```

## License

代码结构可自由参考构建其他 QQ 机器人。嘟嘟鲨鱼的人格、提示词、身份创意受保护，禁止部署相同 persona 的实例。详见 [LICENSE](LICENSE)。
