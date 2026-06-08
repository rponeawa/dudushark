"""
Web UI API 路由。
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.config import (
    DATA_DIR,
    WEBUI_PASSWORD,
    AUTH_ENABLED,
    load_global_config,
    save_global_config,
    get_instance_config,
    save_instance_config,
    get_instance_dir,
    LLMConfig,
)
from server.bot.onebot_handler import onebot_server
from server.bot.message_handler import get_message_handler
from server.bot.mood import get_mood
from server.memory.manager import get_memory_manager
from server.napcat.manager import napcat_manager

logger = logging.getLogger("dudushark.webui")
router = APIRouter(prefix="/api")

TOKEN_SECRET = WEBUI_PASSWORD + "_dudushark_token_salt" if WEBUI_PASSWORD else ""
TOKEN_TTL = 86400 * 7  # 7 天


def _make_token() -> str:
    expires = int(time.time()) + TOKEN_TTL
    payload = f"dudushark:{expires}"
    sig = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    token = f"{payload}:{sig}"
    return _b64(token)


def _verify_token(token: str) -> bool:
    try:
        raw = _unb64(token)
        parts = raw.split(":")
        if len(parts) != 3 or parts[0] != "dudushark":
            return False
        expires = int(parts[1])
        if time.time() > expires:
            return False
        payload = f"dudushark:{expires}"
        expected = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(parts[2], expected)
    except Exception:
        return False


def _b64(s: str) -> str:
    import base64
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s).decode()


@router.post("/auth/login")
async def login(body: dict):
    pw = body.get("password", "") if body else ""
    if AUTH_ENABLED and pw == WEBUI_PASSWORD:
        return {"token": _make_token()}
    if not AUTH_ENABLED:
        return {"token": ""}
    raise HTTPException(401, "密码错误")

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
START_TIME = time.time()

# 最近事件缓冲区
_recent_events: list[dict] = []

# LLM 健康检查缓存（每 60 秒检查一次，避免浪费 API 额度）
_llm_cache: dict = {"ok": None, "ts": 0}

def _add_event(evt: dict):
    evt["_ts"] = time.time()
    _recent_events.insert(0, evt)
    if len(_recent_events) > 100:
        _recent_events.pop()


async def _check_llm() -> bool:
    now = time.time()
    if _llm_cache["ok"] is not None and (now - _llm_cache["ts"]) < 60:
        return _llm_cache["ok"]
    cfg = load_global_config()
    if not cfg.get("instances"):
        return False
    try:
        first_qq = next(iter(cfg["instances"]))
        inst_cfg = get_instance_config(first_qq)
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                inst_cfg.llm.base_url,
                headers={"Authorization": f"Bearer {inst_cfg.llm.api_key}"},
                json={"model": inst_cfg.llm.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
        _llm_cache["ok"] = resp.status_code == 200
    except Exception:
        _llm_cache["ok"] = False
    _llm_cache["ts"] = now
    return _llm_cache["ok"]


# ---- 全局状态 ----

class InstanceInfo(BaseModel):
    qq: str
    connected: bool
    napcat_running: bool


@router.get("/status")
async def system_status():
    """系统整体状态。"""
    cfg = load_global_config()
    instances_status = []
    total_conversations = 0
    total_memories = 0

    for qq in cfg.get("instances", {}):
        client = onebot_server.get_client(qq)
        napcat = napcat_manager.get(qq)
        handler = get_message_handler(qq)
        mem_mgr = get_memory_manager(qq)
        memory_users = mem_mgr.list_users()
        convos = len(handler.list_conversations())
        total_conversations += convos

        # 统计记忆
        for uid in memory_users:
            total_memories += len(mem_mgr.recall_all(uid))

        instances_status.append({
            "qq": qq,
            "connected": client.connected if client else False,
            "napcat_running": (napcat and napcat.is_running) or (client and client.connected),
            "conversation_count": convos,
            "memory_users": len(memory_users),
        })

    # LLM 连通性检查（60 秒缓存）
    llm_ok = await _check_llm()

    return {
        "uptime": round(time.time() - START_TIME),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "memory_mb": round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1),
        "data_dir": str(DATA_DIR),
        "llm_ok": llm_ok,
        "instances": instances_status,
        "total_conversations": total_conversations,
        "total_memories": total_memories,
        "recent_events": _recent_events[-20:],
    }


@router.get("/instances")
async def list_instances():
    cfg = load_global_config()
    instances = []
    for qq in cfg.get("instances", {}):
        client = onebot_server.get_client(qq)
        napcat = napcat_manager.get(qq)
        instances.append(InstanceInfo(
            qq=qq,
            connected=client.connected if client else False,
            napcat_running=(napcat and napcat.is_running) or (client and client.connected),
        ))
    return {"instances": [i.model_dump() for i in instances], "current": cfg.get("current_instance")}


@router.get("/instances/{qq}/status")
async def instance_detail_status(qq: str):
    """单个实例详细状态。"""
    client = onebot_server.get_client(qq)
    napcat = napcat_manager.get(qq)
    handler = get_message_handler(qq)
    mem_mgr = get_memory_manager(qq)
    memory_users = mem_mgr.list_users()

    mem_stats = {}
    for uid in memory_users:
        mem_stats[uid] = len(mem_mgr.recall_all(uid))

    mood = get_mood(qq)
    mood.update()
    return {
        "qq": qq,
        "connected": client.connected if client else False,
        "napcat_running": (napcat and napcat.is_running) or (client and client.connected),
        "napcat_webui_port": napcat.webui_port if napcat else 6099,
        "onebot_ws_port": napcat.ws_port if napcat else 8080,
        "conversation_count": len(handler.list_conversations()),
        "memory_users": memory_users,
        "memory_stats": mem_stats,
        "total_memories": sum(mem_stats.values()),
        "mood": mood.state_dict(),
    }


@router.get("/instances/{qq}/reminders")
async def get_reminders(qq: str):
    from server.config import get_reminders_path
    path = get_reminders_path(qq)
    if not path.exists():
        return {"reminders": []}
    try:
        reminders = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        reminders = []
    return {"reminders": [{"at_utc": r["at_utc"], "content": r["content"], "user_id": r["user_id"], "group_id": r.get("group_id", "")} for r in reminders]}


@router.get("/instances/{qq}/paused_groups")
async def get_paused_groups(qq: str):
    """获取暂停的群列表。"""
    handler = get_message_handler(qq)
    return {"paused_groups": list(handler._paused_groups)}


@router.post("/instances/{qq}/paused_groups/{group_id}/pause")
async def pause_group(qq: str, group_id: str):
    """暂停群消息处理。"""
    handler = get_message_handler(qq)
    handler._paused_groups.add(group_id)
    handler._save_paused_groups()
    logger.info(f"[{qq}] WebUI paused group {group_id}")
    return {"ok": True}


@router.post("/instances/{qq}/paused_groups/{group_id}/resume")
async def resume_group(qq: str, group_id: str):
    """恢复群消息处理。"""
    handler = get_message_handler(qq)
    handler._paused_groups.discard(group_id)
    handler._save_paused_groups()
    logger.info(f"[{qq}] WebUI resumed group {group_id}")
    return {"ok": True}


# ---- QQ 空间 ----

@router.get("/instances/{qq}/qzone/posts")
async def get_qzone_posts(qq: str):
    """获取本地存档的 QQ 空间说说列表。"""
    from server.config import get_qzone_posts_path
    path = get_qzone_posts_path(qq)
    posts = []
    if path.exists():
        try:
            posts = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            posts = []
    return {"posts": posts[:50]}


class QzonePostBody(BaseModel):
    content: str | None = None


@router.post("/instances/{qq}/qzone/post")
async def qzone_manual_post(qq: str, body: QzonePostBody | None = None):
    """手动发一条 QQ 空间说说。content 为空时自动生成。"""
    from server.qzone import QzoneClient

    content = body.content if body and body.content else None
    if not content:
        # 自动生成内容
        handler = get_message_handler(qq)
        from datetime import datetime, timezone, timedelta
        from server.bot.proactive import ProactiveScheduler
        sched_cls = ProactiveScheduler
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        today_diary = handler.memory.recall_by_date("__diary__", today_str)
        if today_diary:
            diary_text = "\n".join(f"- {d['text'][:200]}" for d in today_diary[:5])
            diary_section = f"## 今天发生的事\n{diary_text}"
        else:
            diary_section = "## 今天\n今天没有什么特别的事发生。随便写点什么吧～"
        prompt = sched_cls.QZONE_PROMPT.format(diary_section=diary_section)
        try:
            from server.bot.message_handler import _call_llm
            resp = await _call_llm(
                handler.cfg.llm.base_url,
                handler.cfg.llm.api_key,
                {
                    "model": handler.cfg.llm.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "temperature": 0.9,
                },
            )
            content = resp.strip()
            if content.startswith('"') and content.endswith('"'):
                content = content[1:-1]
            content = content[:200]
        except Exception as e:
            raise HTTPException(500, f"生成内容失败: {e}")

    if not content:
        raise HTTPException(500, "生成内容为空")

    qzone = QzoneClient(qq)
    ok, msg = await qzone.publish_post(content)
    if ok:
        # 保存到本地
        from server.config import get_qzone_posts_path
        path = get_qzone_posts_path(qq)
        path.parent.mkdir(parents=True, exist_ok=True)
        posts = []
        if path.exists():
            try:
                posts = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                posts = []
        posts.insert(0, {"content": content, "created": time.time()})
        posts = posts[:200]
        path.write_text(json.dumps(posts, ensure_ascii=False, indent=2))
        # 存进全局记忆，打标签避免下次生成时引用
        import datetime as _dt
        from server.memory.manager import _CN_TZ
        today = _dt.datetime.now(_CN_TZ).strftime("%Y-%m-%d")
        get_memory_manager(qq).remember("__diary__", "空间说说", f"{today} 说说", content)
        logger.info(f"[{qq}] WebUI manual Qzone post: {content[:50]}")
        return {"ok": True, "content": content}
    else:
        raise HTTPException(500, f"发帖失败: {msg}")


# ---- 代传话 pending 管理 ----

@router.get("/instances/{qq}/pending_relays")
async def list_pending_relays(qq: str):
    handler = get_message_handler(qq)
    return {"pending_relays": handler.get_pending_relays()}


@router.delete("/instances/{qq}/pending_relays/{relay_id}")
async def cancel_pending_relay(qq: str, relay_id: str):
    handler = get_message_handler(qq)
    ok = handler.cancel_pending_relay(relay_id)
    if not ok:
        raise HTTPException(404, "未找到该代传话")
    return {"ok": True}


@router.post("/instances/create")
async def create_instance(qq: str, napcat_path: str = ""):
    cfg = load_global_config()
    inst_cfg = get_instance_config(qq)
    save_instance_config(inst_cfg)
    insts = cfg.get("instances", {})
    insts[qq] = {"created": True}
    cfg["instances"] = insts
    save_global_config(cfg)
    napcat = napcat_manager.create(qq, napcat_path or None)
    napcat.ws_port = inst_cfg.onebot_ws_port
    napcat.webui_port = inst_cfg.napcat_webui_port
    return {"ok": True, "qq": qq}


@router.post("/instances/{qq}/start")
async def start_instance(qq: str):
    napcat = napcat_manager.get(qq)
    if not napcat:
        napcat = napcat_manager.create(qq)
        inst_cfg = get_instance_config(qq)
        napcat.ws_port = inst_cfg.onebot_ws_port
        napcat.webui_port = inst_cfg.napcat_webui_port
    ok = await napcat.start()
    return {"ok": ok, "qq": qq}


@router.post("/instances/{qq}/stop")
async def stop_instance(qq: str):
    napcat = napcat_manager.get(qq)
    if napcat:
        await napcat.stop()
    return {"ok": True}


@router.get("/instances/{qq}/qrcode")
async def get_qrcode(qq: str):
    napcat = napcat_manager.get(qq)
    if not napcat:
        raise HTTPException(404, "实例不存在")
    qr = await napcat.get_qr_code()
    return {"qrcode": qr}


# ---- 配置 ----

@router.get("/instances/{qq}/config")
async def get_config(qq: str):
    return get_instance_config(qq).model_dump()


class ConfigUpdate(BaseModel):
    llm: LLMConfig | None = None
    context_max_tokens: int | None = None
    memory_retrieval_count: int | None = None
    web_search_enabled: bool | None = None
    reply_split_enabled: bool | None = None
    reply_split_max: int | None = None
    group_reply_ratio: float | None = None
    private_merge_delay: float | None = None
    group_merge_delay: float | None = None
    onebot_ws_port: int | None = None
    napcat_webui_port: int | None = None
    proactive_enabled: bool | None = None
    proactive_global_cooldown_sec: int | None = None
    proactive_per_conv_cooldown_sec: int | None = None
    proactive_group_probability: float | None = None
    proactive_private_probability: float | None = None
    proactive_curiosity_threshold: float | None = None
    mood_enabled: bool | None = None
    tts_enabled: bool | None = None
    tts_voice: str | None = None
    tts_model: str | None = None
    asr_model: str | None = None
    asr_prompt: str | None = None
    admins: list[dict] | None = None
    admins_description: str | None = None
    family_memory: str | None = None
    family_note: str | None = None


@router.put("/instances/{qq}/config")
async def update_config(qq: str, body: ConfigUpdate):
    cfg = get_instance_config(qq)
    if body.llm:
        cfg.llm = body.llm
    if body.context_max_tokens is not None:
        cfg.context_max_tokens = body.context_max_tokens
    if body.memory_retrieval_count is not None:
        cfg.memory_retrieval_count = body.memory_retrieval_count
    if body.web_search_enabled is not None:
        cfg.web_search_enabled = body.web_search_enabled
    if body.reply_split_enabled is not None:
        cfg.reply_split_enabled = body.reply_split_enabled
    if body.reply_split_max is not None:
        cfg.reply_split_max = body.reply_split_max
    if body.group_reply_ratio is not None:
        cfg.group_reply_ratio = body.group_reply_ratio
    if body.private_merge_delay is not None:
        cfg.private_merge_delay = body.private_merge_delay
    if body.group_merge_delay is not None:
        cfg.group_merge_delay = body.group_merge_delay
    if body.onebot_ws_port is not None:
        cfg.onebot_ws_port = body.onebot_ws_port
    if body.napcat_webui_port is not None:
        cfg.napcat_webui_port = body.napcat_webui_port
    if body.proactive_enabled is not None:
        cfg.proactive_enabled = body.proactive_enabled
    if body.proactive_global_cooldown_sec is not None:
        cfg.proactive_global_cooldown_sec = body.proactive_global_cooldown_sec
    if body.proactive_per_conv_cooldown_sec is not None:
        cfg.proactive_per_conv_cooldown_sec = body.proactive_per_conv_cooldown_sec
    if body.proactive_group_probability is not None:
        cfg.proactive_group_probability = body.proactive_group_probability
    if body.proactive_private_probability is not None:
        cfg.proactive_private_probability = body.proactive_private_probability
    if body.proactive_curiosity_threshold is not None:
        cfg.proactive_curiosity_threshold = body.proactive_curiosity_threshold
    if body.mood_enabled is not None:
        cfg.mood_enabled = body.mood_enabled
    if body.tts_enabled is not None:
        cfg.tts_enabled = body.tts_enabled
    if body.tts_voice is not None:
        cfg.tts_voice = body.tts_voice
    if body.tts_model is not None:
        cfg.tts_model = body.tts_model
    if body.asr_model is not None:
        cfg.asr_model = body.asr_model
    if body.asr_prompt is not None:
        cfg.asr_prompt = body.asr_prompt
    if body.admins is not None:
        cfg.admins = body.admins
    if body.admins_description is not None:
        cfg.admins_description = body.admins_description
    if body.family_memory is not None:
        cfg.family_memory = body.family_memory
    if body.family_note is not None:
        cfg.family_note = body.family_note
    save_instance_config(cfg)
    handler = get_message_handler(qq)
    handler.reload_config()
    return {"ok": True, "config": cfg.model_dump()}


# ---- 会话管理 ----

@router.get("/instances/{qq}/conversations")
async def list_conversations(qq: str):
    handler = get_message_handler(qq)
    result = []
    for key in handler.list_conversations():
        ctype = handler._convo_types.get(key, "private")
        result.append({"key": key, "type": ctype})
    return {"conversations": result}


@router.get("/instances/{qq}/conversations/{key:path}")
async def get_conversation(qq: str, key: str):
    handler = get_message_handler(qq)
    msgs = handler.get_conversation(key=key)
    return {"key": key, "messages": msgs}


@router.delete("/instances/{qq}/conversations/{key:path}")
async def clear_conversation(qq: str, key: str):
    handler = get_message_handler(qq)
    handler.clear_conversation(key=key)
    return {"ok": True}


# ---- 记忆管理 ----

@router.get("/instances/{qq}/memories/users")
async def list_memory_users(qq: str):
    return {"users": get_memory_manager(qq).list_users()}


@router.get("/instances/{qq}/memories/{user_id}")
async def list_user_memories(qq: str, user_id: str):
    return {"user_id": user_id, "memories": get_memory_manager(qq).recall_all(user_id)}


@router.get("/instances/{qq}/memories/{user_id}/search")
async def search_memories(qq: str, user_id: str, q: str):
    return {"user_id": user_id, "results": get_memory_manager(qq).recall_by_vector(user_id, q)}


@router.delete("/instances/{qq}/memories/{user_id}")
async def clear_user_memories(qq: str, user_id: str):
    get_memory_manager(qq).forget_all(user_id)
    return {"ok": True}


@router.delete("/instances/{qq}/memories/{user_id}/{category}/{title}")
async def delete_memory(qq: str, user_id: str, category: str, title: str):
    get_memory_manager(qq).forget(user_id, category, title)
    return {"ok": True}


class MemoryCreate(BaseModel):
    category: str
    title: str
    content: str


@router.post("/instances/{qq}/memories/{user_id}")
async def create_memory(qq: str, user_id: str, body: MemoryCreate):
    get_memory_manager(qq).remember(user_id, body.category, body.title, body.content)
    return {"ok": True}


# ---- WebSocket 事件推送 ----

_widget_ws: list[WebSocket] = []


@router.websocket("/ws/widget")
async def widget_ws(ws: WebSocket):
    await ws.accept()
    _widget_ws.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _widget_ws.remove(ws)


async def push_event(event: dict):
    _add_event(event)
    dead = []
    for ws in _widget_ws:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _widget_ws.remove(ws)


# ---- 数据备份/恢复 ----

@router.get("/instances/{qq}/backup")
async def backup_data(qq: str):
    """导出所有数据为 zip 文件。"""
    import zipfile, io, os as _os
    from fastapi.responses import StreamingResponse

    data_dir = get_instance_dir(qq).parent.parent  # data/
    env_file = Path(__file__).parent.parent.parent / ".env"
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in _os.walk(str(data_dir)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for f in files:
                fpath = Path(root) / f
                arcname = str(fpath.relative_to(data_dir.parent))
                zf.write(str(fpath), arcname)
        if env_file.exists():
            zf.write(str(env_file), ".env")

    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=dudushark-backup-{qq}.zip"}
    )


@router.post("/instances/{qq}/backup/restore")
async def restore_data(qq: str, backup_file: UploadFile = None):
    """从 zip 恢复数据。聊天记录和记忆合并，配置覆盖。"""
    import zipfile, io, os as _os
    if not backup_file:
        raise HTTPException(400, "请上传 zip 文件")
    if not backup_file.filename or not backup_file.filename.endswith(".zip"):
        raise HTTPException(400, "只支持 .zip 文件")

    data_parent = get_instance_dir(qq).parent.parent.parent  # dudushark/
    content = await backup_file.read()

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for member in zf.namelist():
            if member.startswith("/") or ".." in member or member.endswith("/"):
                continue
            target = data_parent / member

            # 对话 JSONL：合并而非覆盖
            if "/conversations/" in member and member.endswith(".jsonl"):
                _merge_jsonl(target, zf, member)
                continue

            # 记忆 MD 文件：合并而非覆盖
            if "/memories/" in member and member.endswith(".md"):
                _merge_memory_md(target, zf, member)
                continue

            # 其他文件（配置、ChromaDB 等）：直接覆盖
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(str(target), "wb") as dst:
                dst.write(src.read())

    _os.system("sudo systemctl restart dudushark &")
    return {"ok": True, "message": "数据已合并恢复，服务正在重启"}


def _merge_jsonl(target: Path, zf, member: str):
    """合并 JSONL 对话记录：追加备份中的新消息（按 ts 去重）。"""
    import json as _json
    existing_ts = set()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                existing_ts.add(_json.loads(line.strip()).get("ts", 0))
            except Exception:
                pass
    merged = []
    if target.exists():
        merged = target.read_text(encoding="utf-8").rstrip("\n").split("\n")
    with zf.open(member) as src:
        for line in src.read().decode("utf-8").splitlines():
            try:
                ts = _json.loads(line.strip()).get("ts", 0)
                if ts not in existing_ts:
                    merged.append(line.strip())
                    existing_ts.add(ts)
            except Exception:
                pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(merged) + "\n", encoding="utf-8")


def _merge_memory_md(target: Path, zf, member: str):
    """合并记忆 MD 文件：备份中的内容如不存在则写入。"""
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(str(target), "wb") as dst:
            dst.write(src.read())


# ---- 实时日志 WebSocket ----

@router.websocket("/ws/logs")
async def logs_ws(ws: WebSocket):
    await ws.accept()
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", "dudushark", "-f", "--no-pager", "-o", "cat", "-n", "200",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                await ws.send_text(line.decode("utf-8", errors="replace").rstrip("\n"))
        finally:
            proc.kill()
            await proc.wait()
    except WebSocketDisconnect:
        pass
