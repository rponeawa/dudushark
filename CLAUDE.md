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
| `tts_enabled` | 语音发送开关 |
| `tts_voice` | TTS 音色，默认 `ruanmengnvsheng`（软萌女声） |
| `tts_model` | TTS 模型，默认 `step-tts-2` |
| `asr_model` | 语音转文字模型，默认 `step-audio-2` |
| `asr_prompt` | 转写提示词 |
| `DUDUSHARK_DATA` | 数据目录，默认 `./data` |

## 架构

```
NapCatQQ (Docker: mlikiowa/napcat-docker)
  └─ WS → ws://172.17.0.1:8080/onebot/v11/ws/{qq}   (OneBot v11 反向WS)
            └─ server/main.py                          (FastAPI + WS 端点)
                 ├─ server/config.py                   (全局配置 + BotConfig 模型定义)
                 ├─ bot/onebot_handler.py              (OneBot 协议解析 + 多模态图片提取)
                 ├─ bot/message_handler.py             (消息合并缓冲 → LLM 调用 → 回复拆分)
                 ├─ bot/persona.py                     (System prompt 人设定义)
                 ├─ bot/mood.py                         (全局心情/睡眠：影响所有回复+主动发言)
                 ├─ memory/manager.py                  (按 user_id 分目录的 MD 记忆 CRUD)
                 ├─ memory/vector_store.py             (ChromaDB + SiliconFlow embedding)
                 ├─ memory/context.py                  (上下文压缩，摘要合并)
                 ├─ search/bing.py                     (Bing/DDG HTML 解析搜索)
                 ├─ bot/proactive.py                   (主动消息调度 + 提醒触发 + QQ空间发帖)
                 ├─ qzone.py                            (QQ 空间说说 API：发帖 + 获取列表)
                 ├─ napcat/manager.py                  (NapCatQQ 配置生成，WebUI API 交互)
                 └─ webui/routes.py                    (REST API + WS 事件推送)
```

## 核心数据流

**消息处理链路：**
1. NapCatQQ 通过反向 WS 发送 OneBot 事件 → `onebot_handler._dispatch()`
2. 图片/表情包/语音消息提取。图片和表情包通过 `sub_type` 区分，语音触发 ASR 转写
3. 消息类型事件用 `create_task` 调度到后台，不阻塞 WS 接收循环
4. `message_handler.handle()` 使用 Future 机制合并同用户连续消息
5. 合并窗口到期后调用 LLM 生成回复，`[SKIP]` 表示不回复
6. LLM 返回 JSON：`{"reply":"...","quote":bool,"quote_index":null|int,"voice":null|"all"|"last","voice_emotion":null|"...","memory":null|{...},"diary":null|{...},"group_memory":null|{...},"forget":null|{...},"remind":null|{...},"relay":null|{...},"qzone":null|"...","search":"..."}`
7. `voice` 非 null 时：发送语音（StepFun TTS step-tts-2），`voice_emotion` 控制情绪
8. 长回复按 `。！？\n～` 断句拆分发送，不限段数，间隔 `max(2.0, len*0.08+1.0)`
9. `[SKIP]` 不回复，LLM 调用失败返回 `[]` 不回复
10. LLM 调用指数退避重试（3次, 2/4/8s），速率限制（滑动窗口 8次/60s）
11. 多模态：图片以 `[{"type":"text","text":"..."},{"type":"image_url",...}]` 格式传入

**消息合并：**
- `handle()` 返回 `list[ReplyPart]`，私聊合并等 8s、群聊 9s
- 群聊所有说话人合并到同一窗口，私聊最大窗口 60s
- 合并格式 `[N] name: text`，LLM 通过序号区分说话人
- `names_map` 追踪名字→user_id，memory 的 `user` 字段指定归属

**群聊 SKIP 系统（三层）：**
1. 主 LLM 自行判断是否回复（包括 @鱼/戳一戳——生气可 SKIP）
2. 主 LLM 决定回复后 → 独立 LLM 二次验证（只看最近 10 分钟上下文+人设）
3. 睡眠时段（22-7）附加"正在睡觉"提示，SKIP 概率大幅提高

**记忆系统：**
- 每人独立 ChromaDB collection（`mem_{safe_user_id}`），向量检索完全隔离
- memory/diary/forget 由独立 LLM 二次判断是否值得记录（`_should_record_memory`）
- 群聊合并消息时检索所有说话人的记忆（去重+按分数排序）
- 已有标题列表展示给 LLM：`（已有记忆条目: 类别/标题, ...）`
- 相同 category+title → upsert 更新，不同 → 新建
- `__diary__` 全局记忆，`__group__<id>` 群聊记忆
- ChromaDB collection 名使用 `strip("_")` 清理非法字符

**语音系统：**
- ASR（语音转文字）：收到 `record` 段 → `get_record` API 转 wav → `docker exec cat` 读文件 → step-audio-2 转写
- TTS（文字转语音）：LLM 通过 `voice` JSON 字段决定是否发语音，`voice_emotion` 选情绪
- 大部分时候 null。撒娇卖萌/对方要求/特别开心时发 `last`（最后一段语音，比较常用），`all`（整段语音，很少用）
- `/say [情绪] 文本` 管理员命令测试语音（私聊/群聊均可，不落 JSONL）
- 情绪：撒娇/高兴/非常高兴/悲伤/生气/非常生气/兴奋/惊讶/困惑/恐惧
- TTS 模型/音色、ASR 模型/提示词均在 WebUI 可配

**QQ 空间系统：**
- 认证：NapCat `get_credentials`/`get_cookies` 获取 `qzone.qq.com` Cookie → `p_skey` 计算 `g_tk`
- 每次 API 调用都重新获取 Cookie 避免过期
- 发帖：POST `emotion_cgi_publish_v6`，读取：GET `emotion_cgi_msglist_v6`
- **管理员触发发帖**：管理员消息含"空间/说说/动态"关键词 → 注入 qzone JSON 字段 → 主 LLM 自行判断是否填内容 → 独立 LLM 二次验证（`_should_post_qzone`）→ 通过后异步发帖
- **每日自动发帖**：清醒时段（8-22点）10% 概率触发，内容基于当天 diary 记忆，无则随机
- 发帖记录保存在 `data/instances/{qq}/qzone_posts.json`，最多 200 条
- 发帖状态（当天是否已发）保存在 `data/instances/{qq}/qzone_state.json`
- WebUI 可手动触发发帖（自动生成内容）并查看历史

**对话持久化：**
- JSONL 文件落盘 `data/instances/{qq}/conversations/{key}.jsonl`
- 启动恢复，无条数上限，`_convo_types` 记录群聊/私聊类型

**上下文压缩：**
- 群聊 8000 token 预算（reserve_for_reply=1500），私聊全量
- 多次压缩摘要自动合并（`_coalesce_summaries`）
- 群聊共享对话历史（key=group_id），私聊各自独立

**全局心情系统：**
- `DuduMood` 单例，被 proactive scheduler 和 message handler 共享
- 22:00-07:00 固定犯困/睡着（energy 5-8%）
- 07:00-22:00 清醒，10% 概率随机犯困，醒来后 energy×2
- `system_mood_context()` 注入系统 prompt
- 前端实时显示睡眠状态 + 精力条（最高 100%）

**主动消息 + 提醒：**
- 欲望驱动：`curiosity_threshold × energy` 一次随机决定是否说话（默认 0.15 × 精力）
- **全局冷却**：两次主动消息间隔至少 30 分钟（`proactive_global_cooldown_sec: 1800`）
- `_relationship_warmth()` 相对评分选人：以所有私聊中最活跃者的交换数为基准，亲密度 = 自己交换数 / 最高交换数 × 时间衰减
- **私聊门槛**：亲密度 < 0.7 不主动找（不够熟不打扰）
- 对方当天没给嘟嘟发过消息不主动找；睡眠时段不发言
- 提醒始终私聊发送，不受 sleep/cooldown 限制
- 有提醒时不创建记忆（避免重复存储）

**管理员群聊控制：**
- `/pause` — 群内管理员发送，暂停该群消息处理（不 LLM、不落盘）
- `/resume` — 仅在暂停状态下由管理员发送，恢复消息处理
- 暂停期间除 `/resume` 外所有消息静默忽略

**管理员代传话（三层防护）：**
- 主 LLM 输出 relay → 独立 LLM 验证（无上文，只看原始消息）→ 30s 去重
- 仅管理员私聊可用，群聊完全不注入 relay 指令
- 家族记忆仅 role 含"妈"的成员在私聊中注入

**Prompt 缓存优化：**
- 消息顺序：[0]persona(固定→缓存命中) [1]json_prompt(静态，时间已分离) [2]当前时间(独立消息) [3]mood [4]family [5]diary [6]group [7]memories [8]family_note [9]relay [10+]history → user_msg
- 时间从 json_prompt 分离到独立消息，避免 json_prompt 每 1-2 分钟整体 cache miss
- family_note/relay/voice_note/memory_note 全部移到 history 和 user_msg 之前
- persona + json_prompt 稳定缓存，动态内容集中在后面

**JSON 格式指令：**
- memory: `{"user":"名字","category":"类别","title":"标题","content":"内容"}` — user 字段指定归属
- diary: 同 memory 格式，值得写才写
- forget: `{"category":"类别","title":"标题"}` — 删除记忆
- remind: `{"at_utc": Unix秒, "content": "提醒内容"}` — 一次性定时提醒
- relay: `{"to_role": "角色名", "content": "转达内容"}` — 管理员间代传话
- qzone: 字符串，QQ 空间说说内容。仅管理员消息且含关键词时字段可见，主 LLM 自行判断是否填
- search: `"search":"关键词"` — LLM 请求网络搜索，与 reply 同时输出，系统异步执行搜索+二次 LLM

**角色/管理员系统：**
- `BotConfig.admins` 列表，运行时 QQ 匹配 → 用户名后标注【角色】标签
- `admins_description`：仅管理员私聊注入 system prompt
- `family_memory` + `family_note`：仅 role 含"妈"的成员私聊注入
- 群聊不注入任何管理员描述和家族记忆

**隐私铁律：**
- 绝对不泄露他人记忆、私聊内容、个人信息
- 冒充者无法获取角色标签
- 群聊不暴露全局记忆中的人名

**NapCatQQ (Docker)：**
- 使用 `mlikiowa/napcat-docker:latest` 镜像
- 端口 6099 映射，配置目录 `~/NapCatQQ/config/` 挂载
- OneBot 反向 WS 连接 `ws://172.17.0.1:8080/onebot/v11/ws/{qq}`

## 多实例隔离

每个 QQ 号拥有独立的数据目录，配置和记忆完全隔离。

```
data/instances/{qq}/
  ├── bot_config.json
  ├── memories/{user_id}/*.md
  ├── chroma/
  ├── conversations/{key}.jsonl
  ├── reminders.json
  ├── qzone_posts.json
  └── qzone_state.json
```

## API 路由

所有 API 前缀 `/api`，WebSocket `/api/ws/widget`。除 `/api/auth/login` 和 `/api/ws/widget` 外均需 Bearer token 鉴权。

关键端点：`/auth/login`、`/status`、`/instances` CRUD、`/instances/{qq}/config`、`/instances/{qq}/memories/{user_id}`、`/instances/{qq}/conversations/{key}`、`/instances/{qq}/reminders`、`/instances/{qq}/qzone/posts`、`/instances/{qq}/paused_groups`。
