"""
QQ 空间 API — 发说说 / 获取说说列表。

认证通过 NapCat 的 get_credentials / get_cookies 获取 qzone.qq.com 的 Cookie，
从 p_skey 计算 g_tk（DJB2 变体）。每次操作都重新获取 Cookie 以避免过期。
"""

import json
import logging
import re as _re
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("dudushark.qzone")

PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
MSGLIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_msglist_v6"


def _calc_gtk(skey: str) -> str:
    """从 p_skey / skey 计算 g_tk（DJB2 变体，种子 5381）。"""
    h = 5381
    for ch in skey:
        h += (h << 5) + ord(ch)
    return str(h & 0x7FFFFFFF)


def _extract_skey(cookie_str: str) -> str | None:
    """从 Cookie 字符串中提取 p_skey，退回 skey。"""
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if pair.startswith("p_skey="):
            return pair.split("=", 1)[1]
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if pair.startswith("skey="):
            return pair.split("=", 1)[1]
    return None


class QzoneClient:
    """QQ 空间操作客户端。每次操作重新认证。"""

    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq

    async def _init_session(self, client) -> bool:
        """获取 Cookie 并计算 g_tk。每次调用都重新获取。

        Args:
            client: OneBotClient 实例
        Returns:
            True if initialized successfully
        """
        try:
            login_info = await client.call_api("get_login_info")
            self.uin = str(login_info.get("data", login_info).get("user_id", ""))
        except Exception as e:
            logger.error(f"[{self.bot_qq}] get_login_info failed: {e}")
            return False

        cookie_str = ""
        # Primary: get_credentials
        try:
            creds = await client.call_api("get_credentials", {"domain": "qzone.qq.com"})
            cookie_str = creds.get("data", creds).get("cookies", "")
        except Exception:
            pass

        # Fallback: get_cookies
        if not cookie_str:
            try:
                creds = await client.call_api("get_cookies", {"domain": "qzone.qq.com"})
                cookie_str = creds.get("data", creds).get("cookies", "")
            except Exception as e:
                logger.error(f"[{self.bot_qq}] get_cookies failed: {e}")
                return False

        if not cookie_str:
            logger.error(f"[{self.bot_qq}] empty cookie from NapCat")
            return False

        skey = _extract_skey(cookie_str)
        if not skey:
            logger.error(f"[{self.bot_qq}] no p_skey/skey in cookie")
            return False

        self.cookie = cookie_str
        self.gtk = _calc_gtk(skey)
        return True

    async def publish_post(self, content: str) -> tuple[bool, str]:
        """发一条说说。

        Returns:
            (success, message)
        """
        from server.bot.onebot_handler import onebot_server

        ob_client = onebot_server.get_client(self.bot_qq)
        if not ob_client or not ob_client.connected:
            return False, "NapCat 未连接"

        if not await self._init_session(ob_client):
            return False, "Cookie 获取失败"

        params = urlencode({
            "syn_tweet_verson": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "fs",
            "qzreferrer": f"https://user.qzone.qq.com/{self.uin}/infocenter",
        })

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": self.cookie,
            "Origin": "https://user.qzone.qq.com",
            "Referer": f"https://user.qzone.qq.com/{self.uin}/infocenter",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.post(
                    f"{PUBLISH_URL}?g_tk={self.gtk}",
                    content=params,
                    headers=headers,
                )
                raw = resp.text
                # Response is HTML-wrapped JSONP:
                # <html><body><script>...cb=frameElement.callback;...cb({"code":0,...});</script></body></html>
                # Find the last "cb(" and extract balanced braces
                data = None
                cb_pos = raw.rfind("cb(")
                if cb_pos >= 0:
                    json_start = raw.find("{", cb_pos)
                    if json_start >= 0:
                        depth = 0
                        json_end = json_start
                        for i in range(json_start, len(raw)):
                            if raw[i] == "{":
                                depth += 1
                            elif raw[i] == "}":
                                depth -= 1
                                if depth == 0:
                                    json_end = i + 1
                                    break
                        try:
                            data = json.loads(raw[json_start:json_end])
                        except json.JSONDecodeError:
                            return False, f"JSON 解析失败: {raw[json_start:json_end][:200]}"
                if data is None:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        return False, f"非 JSON 响应: {raw[:300]}"
                code = data.get("code", -1)
                msg_text = data.get("message", "") or data.get("msg", "")
                if code == 0:
                    logger.info(f"[{self.bot_qq}] Qzone post OK: {content[:50]}...")
                    return True, "ok"
                else:
                    return False, f"code={code} msg={msg_text}"
        except Exception as e:
            return False, f"异常: {e}"

    async def get_posts(self, num: int = 20, pos: int = 0) -> list[dict]:
        """获取说说列表。

        Args:
            num: 获取条数
            pos: 起始位置（分页）
        Returns:
            说说列表 [{"content": str, "created_time": int, "tid": str}, ...]
        """
        from server.bot.onebot_handler import onebot_server

        ob_client = onebot_server.get_client(self.bot_qq)
        if not ob_client or not ob_client.connected:
            return []

        if not await self._init_session(ob_client):
            return []

        params = {
            "g_tk": self.gtk,
            "uin": self.uin,
            "ftype": "0",
            "sort": "0",
            "pos": str(pos),
            "num": str(num),
            "replynum": "0",
            "code_version": "1",
            "format": "json",
        }

        headers = {
            "Cookie": self.cookie,
            "Referer": f"https://user.qzone.qq.com/{self.uin}/infocenter",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.get(MSGLIST_URL, params=params, headers=headers)
                data = resp.json()
                msglist = data.get("msglist", [])
                result = []
                for item in msglist:
                    content = item.get("content", "")
                    # content 可能是 list of segments
                    if isinstance(content, list):
                        content = "".join(
                            seg.get("text", seg) if isinstance(seg, dict) else str(seg)
                            for seg in content
                        )
                    result.append({
                        "content": content,
                        "created_time": item.get("created_time", 0),
                        "tid": item.get("tid", ""),
                    })
                return result
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Qzone get_posts error: {e}")
            return []
