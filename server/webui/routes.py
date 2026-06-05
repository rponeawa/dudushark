"""
Web UI API 路由。
"""

import logging
import os
import sys
import time
from pathlib import Path

import httpx
import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.config import (
    DATA_DIR,
    load_global_config,
    save_global_config,
    get_instance_config,
    save_instance_config,
    LLMConfig,
)
from server.bot.onebot_handler import onebot_server
from server.bot.message_handler import get_message_handler
from server.bot.mood import get_mood
from server.memory.manager import get_memory_manager
from server.napcat.manager import napcat_manager

logger = logging.getLogger("dudushark.webui")
router = APIRouter(prefix="/api")

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
            "napcat_running": napcat.is_running if napcat else False,
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
            napcat_running=napcat.is_running if napcat else False,
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
        "napcat_running": napcat.is_running if napcat else False,
        "napcat_webui_port": napcat.webui_port if napcat else 6099,
        "onebot_ws_port": napcat.ws_port if napcat else 8080,
        "conversation_count": len(handler.list_conversations()),
        "memory_users": memory_users,
        "memory_stats": mem_stats,
        "total_memories": sum(mem_stats.values()),
        "mood": mood.state_dict(),
    }


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
    admins: list[dict] | None = None


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
    if body.admins is not None:
        cfg.admins = body.admins
    save_instance_config(cfg)
    handler = get_message_handler(qq)
    handler.reload_config()
    return {"ok": True, "config": cfg.model_dump()}


# ---- 会话管理 ----

@router.get("/instances/{qq}/conversations")
async def list_conversations(qq: str):
    handler = get_message_handler(qq)
    return {"conversations": handler.list_conversations()}


@router.get("/instances/{qq}/conversations/{key:path}")
async def get_conversation(qq: str, key: str):
    handler = get_message_handler(qq)
    parts = key.split(":")
    user_id = parts[0]
    group_id = parts[1] if len(parts) > 1 else ""
    msgs = handler.get_conversation(user_id, group_id)
    return {"key": key, "messages": msgs}


@router.delete("/instances/{qq}/conversations/{key:path}")
async def clear_conversation(qq: str, key: str):
    handler = get_message_handler(qq)
    parts = key.split(":")
    user_id = parts[0]
    group_id = parts[1] if len(parts) > 1 else ""
    handler.clear_conversation(user_id, group_id)
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
