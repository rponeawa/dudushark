"""
嘟嘟鲨鱼 QQ 机器人 — 主入口
FastAPI 服务 + OneBot WebSocket + React SPA
"""

import asyncio
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.bot.onebot_handler import onebot_server
from server.bot.message_handler import get_message_handler
from server.bot.mood import remove_mood
from server.bot.proactive import start_scheduler, stop_scheduler
from server.config import DATA_DIR, AUTH_ENABLED, WEBUI_PASSWORD
from server.webui.routes import router as webui_router, push_event, _verify_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("dudushark")

STATIC_DIR = Path(__file__).parent / "webui" / "static"
INDEX_HTML = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("嘟嘟鲨鱼 正在启动... 啊呜～")
    logger.info(f"数据目录: {DATA_DIR}")
    yield
    for qq in onebot_server.list_clients():
        stop_scheduler(qq)
        remove_mood(qq)
    logger.info("嘟嘟鲨鱼 要睡觉了... 啊呜～晚安～")


app = FastAPI(title="嘟嘟鲨鱼 DuduShark", version="1.0.0", lifespan=lifespan)
app.include_router(webui_router)

# Auth middleware — protects /api/* except login and websocket
_AUTH_WHITELIST = {"/api/auth/login", "/api/ws/widget"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if AUTH_ENABLED and request.url.path.startswith("/api/") and request.url.path not in _AUTH_WHITELIST:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or not _verify_token(token):
            return JSONResponse({"detail": "需要登录"}, status_code=401)
    return await call_next(request)

# 静态资源
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/favicon.ico")
async def favicon():
    fav = STATIC_DIR / "favicon.ico"
    return FileResponse(fav) if fav.exists() else HTMLResponse(status_code=404)


@app.websocket("/onebot/v11/ws/{qq}")
async def onebot_ws(ws: WebSocket, qq: str):
    """NapCatQQ 反向 WebSocket 连接端点。"""
    logger.info(f"OneBot 连接: QQ={qq}")

    async def on_connect(bot_qq: str):
        await push_event({"type": "bot_connected", "qq": bot_qq})
        client = onebot_server.get_client(bot_qq)
        if client:
            try:
                info = await client.get_login_info()
                await push_event({"type": "login_info", "qq": bot_qq, "data": info})
            except Exception:
                pass
        start_scheduler(bot_qq)

    async def on_disconnect(bot_qq: str):
        stop_scheduler(bot_qq)
        await push_event({"type": "bot_disconnected", "qq": bot_qq})

    async def on_message(**kwargs):
        handler = get_message_handler(kwargs["bot_qq"])
        replies = await handler.handle(
            user_id=kwargs["user_id"],
            user_name=kwargs["user_name"],
            text=kwargs["text"],
            group_id=kwargs["group_id"],
            msg_type=kwargs["msg_type"],
            message_id=kwargs.get("message_id", ""),
            images=kwargs.get("images", []),
        )
        if not replies:
            return

        client = onebot_server.get_client(kwargs["bot_qq"])
        if not client or not client.connected:
            return

        is_group = bool(kwargs["group_id"])
        target = kwargs["group_id"] if is_group else kwargs["user_id"]

        logger.info(f"[发送] 共{len(replies)}条回复")
        for i, part in enumerate(replies):
            try:
                text = re.sub(r"^>>\s*", "", part.text)  # regex strip >>
                quote_id = part.quote_msg_id
                if quote_id:
                    if is_group:
                        await client.send_group_msg_quote(target, text, quote_id)
                    else:
                        await client.send_private_msg_quote(kwargs["user_id"], text, quote_id)
                else:
                    if is_group:
                        await client.send_group_msg(target, text)
                    else:
                        await client.send_private_msg(kwargs["user_id"], text)
                if i < len(replies) - 1:
                    # 模拟打字时间：每字 ~0.08s + 基础 1s，最少 2s
                    typing_delay = max(2.0, len(text) * 0.08 + 1.0)
                    logger.info(f"[发送] {i+1}/{len(replies)} 延迟 {typing_delay:.1f}s ({len(text)}字)")
                    await asyncio.sleep(typing_delay)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")

        conv_key = handler._conv_key(kwargs["user_id"], kwargs["group_id"])
        merged_text = handler.pop_last_combined(conv_key)
        await push_event({
            "type": "message",
            "qq": kwargs["bot_qq"],
            "user_id": kwargs["user_id"],
            "user_name": kwargs["user_name"],
            "group_id": kwargs["group_id"],
            "text": merged_text or kwargs["text"],
            "reply": "\n---\n".join(p.text for p in replies),
            "quoted": any(p.quote_msg_id for p in replies),
            "target": target,
        })

    client = onebot_server.create_client(
        bot_qq=qq,
        on_message=on_message,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
    )
    await client.handle_ws(ws)
    onebot_server.remove_client(qq)


# SPA fallback — 非 API/WS/静态资源路由返回 index.html
@app.get("/{path:path}")
async def spa_fallback(request: Request, path: str):
    if path.startswith("api/") or path.startswith("onebot/"):
        raise HTTPException(status_code=404)
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return HTMLResponse(
        "<html><body><h1>嘟嘟鲨鱼</h1><p>前端未构建。请运行: cd web && npm run build</p></body></html>"
    )


if __name__ == "__main__":
    import uvicorn

    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    uvicorn.run(app, host=host, port=port)
