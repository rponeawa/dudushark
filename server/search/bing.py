"""
必应 (Bing) 搜索 — 不依赖 API，直接解析搜索结果页。
搜索引擎会自然地对对话中的不确定信息进行补充。
"""

import re
import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def bing_search(query: str, max_results: int = 5) -> list[dict]:
    """搜索并返回结果列表 [{title, url, snippet}]。"""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "zh-CN"},
                headers=HEADERS,
            )
            resp.raise_for_status()
    except Exception:
        return await _fallback_search(query, max_results)

    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for li in soup.select("li.b_algo, .b_result, article"):
        a = li.select_one("h2 a, a[href]")
        if not a:
            continue
        title = a.get_text(strip=True)
        url = a.get("href", "")
        snippet_el = li.select_one(".b_caption p, .b_lineclamp2, .b_algoSlug, p")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    # 如果 Bing 什么都没拿到，尝试 DDG fallback
    if not results:
        return await _fallback_search(query, max_results)
    return results


async def _fallback_search(query: str, max_results: int) -> list[dict]:
    """备用搜索引擎。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=HEADERS,
            )
            resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for el in soup.select(".result"):
        a = el.select_one(".result__a")
        if not a:
            continue
        title = a.get_text(strip=True)
        url = a.get("href", "")
        snippet_el = el.select_one(".result__snippet")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def format_search_results(results: list[dict]) -> str:
    """将搜索结果格式化为 LLM 可读的文本。"""
    if not results:
        return "（未找到相关结果）"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet'][:200]}\n   链接: {r['url']}")
    return "\n\n".join(lines)


def needs_search(text: str) -> bool:
    """判断是否需要搜索：用户提问包含时效性、事实性问题时返回 True。"""
    triggers = [
        r"(?:最近|最新|今天|昨天|现在|当前|刚刚|最近几天)",
        r"(?:新闻|发生了|出了什么|有什么新)",
        r"(?:是多少|多少钱|价格|天气|温度|汇率|股价)",
        r"(?:是什么|什么是|什么意思|定义|解释一下)",
        r"(?:怎么(?:做|办|弄|处理|解决))",
        r"(?:在哪里|什么地方|哪个地方|地址)",
        r"\?|？",
    ]
    score = sum(1 for pat in triggers if re.search(pat, text))
    return score >= 2 or ("?" in text or "？" in text)
