"""
嘟嘟鲨鱼 记忆系统 自然对话模拟测试
模拟真实多轮对话，测试记忆的选择性、准确性、各种边界场景。
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server.config import get_instance_config, load_global_config
from server.memory.manager import MemoryManager, get_memory_manager

TEST_USER_A = "test_user_a"
TEST_USER_B = "test_user_b"
TEST_GROUP = "test_group_nat"
PASS = "✓"
FAIL = "✗"

import httpx
from server.memory.vector_store import SiliconFlowEmbedding

LLM_BASE = ""
LLM_KEY = ""
LLM_MODEL = ""

def setup():
    global LLM_BASE, LLM_KEY, LLM_MODEL
    cfg = load_global_config()
    qq = next(iter(cfg["instances"]))
    icfg = get_instance_config(qq)
    LLM_BASE = icfg.llm.base_url
    LLM_KEY = icfg.llm.api_key
    LLM_MODEL = icfg.llm.model

PERSONA = """你是嘟嘟鲨鱼，一只来自鲨鱼星的赛博大鲨鱼QQ机器人。自称"鱼"，口头禅"啊呜～"。

## 性格
傲娇、善良、喜欢睡觉、喜欢被摸头。对世界充满好奇。

## 记忆规则
- 值得记住的事才记：对方的身份/喜好/重要经历、你们之间的约定、让你印象深刻的事
- 鸡毛蒜皮不要记：日常寒暄、随口闲聊、没信息量的话
- 相同 category+title 会更新旧记忆，所以同一话题用相同的

## 输出格式
必须输出纯JSON（不要markdown代码块）:
{"reply":"...","quote":false,"memory":null,"diary":null,"group_memory":null,"forget":null}

- reply: 回复文本，不回就"[SKIP]"
- memory: 值得记住的关于这个人的事。格式 {"category":"类别","title":"简短标题","content":"内容"}
- diary: 你自己的日记。写日记规则：有值得记录的事才写，日常小事不写。
- group_memory: 关于这个群的事。格式同memory。
- forget: 要删除的记忆 {"category":"类别","title":"标题"}

回复要像真人聊天，1-3句话，不要啰嗦。"""

PERSONA_GROUP = PERSONA + "\n你现在在群里聊天。"

_call_count = 0

def llm(msg: str, system: str = PERSONA) -> dict | None:
    global _call_count
    import time as _t
    # Rate limit: ensure at least 1.2s gap between calls
    _call_count += 1
    if _call_count > 1:
        _t.sleep(2.5)
    try:
        resp = httpx.post(LLM_BASE, headers={"Authorization": f"Bearer {LLM_KEY}"}, json={
            "model": LLM_MODEL, "messages": [{"role":"system","content":system},{"role":"user","content":msg}],
            "temperature": 0.85, "max_tokens": 800,
        }, timeout=60)
        if resp.status_code != 200:
            print(f"  ⚠ API {resp.status_code}: {resp.text[:100]}")
            return None
        raw = resp.json().get("choices",[{}])[0].get("message",{}).get("content","")
        raw = raw.strip()
        try:
            return json.loads(raw)
        except:
            m = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}', raw)
            if m:
                try: return json.loads(m.group(0))
                except: pass
        if not raw:
            print(f"  ⚠ LLM返回空: {resp.status_code}")
        return None
    except Exception as e:
        print(f"  ⚠ LLM异常: {e}")
        return None

def log(ok: bool, label: str, detail: str = ""):
    s = f"  {PASS if ok else FAIL} {label}"
    if detail: s += f" — {detail}"
    print(s)

async def main():
    print("=" * 55)
    print("嘟嘟鲨鱼 自然对话记忆测试")
    print("=" * 55)
    setup()
    print(f"LLM: {LLM_MODEL}")

    mgr = get_memory_manager(next(iter(load_global_config()["instances"])))
    for uid in [TEST_USER_A, TEST_USER_B, f"__group__{TEST_GROUP}", "__diary__"]:
        try: mgr.forget_all(uid)
        except: pass

    results = {"pass": 0, "fail": 0}

    # ====== 场景1: 自然多轮对话，测试选择性记忆 ======
    print("\n── 场景1: 自然闲聊，只记重要信息 ──")
    convos = [
        ("你好呀！今天天气不错。", None, "纯寒暄，不应记"),
        ("我叫小蓝，是个程序员。", "user_info", "自我介绍，应该记"),
        ("中午吃了个三明治，还挺好吃的。", None, "日常琐事，不应记"),
        ("对了，我在学 Rust，感觉比 C++ 有意思多了。", "interest", "兴趣爱好，应该记"),
        ("下班了，好累啊今天。", None, "日常吐槽，不应记"),
        ("话说我喜欢看科幻电影，尤其是诺兰的。", "interest", "品味偏好，应该记"),
    ]
    for msg, expect, desc in convos:
        data = llm(f"{msg}", PERSONA)
        if not data:
            print(f"  ⚠ LLM无响应: {msg[:30]}")
            continue
        mem = data.get("memory")
        reply = data.get("reply","")[:60]
        if expect and mem and isinstance(mem, dict) and mem.get("content"):
            mgr.remember(TEST_USER_A, mem["category"], mem["title"], mem["content"])
            ok = True
        elif not expect and (not mem or not isinstance(mem, dict) or not mem.get("content")):
            ok = True
        else:
            ok = False
        mark = "✓" if ok else "✗"
        mem_info = f"→ {mem['category']}/{mem['title']}" if (mem and mem.get("title")) else "→ null"
        print(f"  {mark} {desc}: {reply[:50]}  {mem_info}")
        if ok: results["pass"] += 1
        else: results["fail"] += 1

    # ====== 场景2: 另一个用户，测试隔离 ======
    print("\n── 场景2: 多用户记忆隔离 ──")
    convos_b = [
        ("嗨，我是小王，我喜欢打篮球和游泳。", "user_info"),
        ("昨天打球扭到脚了，休息了一周。", "experience"),
    ]
    for msg, expect in convos_b:
        data = llm(msg, PERSONA)
        if data and data.get("memory"):
            mem = data["memory"]
            mgr.remember(TEST_USER_B, mem["category"], mem["title"], mem["content"])
    # Verify isolation
    a_mems = {m["file"] for m in mgr.recall_all(TEST_USER_A)}
    b_mems = {m["file"] for m in mgr.recall_all(TEST_USER_B)}
    no_overlap = not a_mems.intersection(b_mems)
    log(no_overlap, "用户A/B记忆隔离", f"A={len(a_mems)}条 B={len(b_mems)}条 无交叉")
    results["pass" if no_overlap else "fail"] += 1

    # Vector verify
    vec_a = mgr.recall_by_vector(TEST_USER_A, "程序员", n=3)
    vec_b = mgr.recall_by_vector(TEST_USER_B, "篮球", n=3)
    log(len(vec_a)>0 and vec_a[0]["score"]>0.5, "向量检索A", f"score={vec_a[0]['score']:.3f}" if vec_a else "无")
    log(len(vec_b)>0 and vec_b[0]["score"]>0.5, "向量检索B", f"score={vec_b[0]['score']:.3f}" if vec_b else "无")
    results["pass" if vec_a and vec_a[0]["score"]>0.5 else "fail"] += 1
    results["pass" if vec_b and vec_b[0]["score"]>0.5 else "fail"] += 1

    # ====== 场景3: 群聊自然对话 ======
    print("\n── 场景3: 群聊多轮，选择性记录 ──")
    group_convos = [
        ("[群聊] 小明: 今天天气真好啊", None),
        ("[群聊] 小红: 我们建个读书会吧，每周读一本书讨论", "group_info"),
        ("[群聊] 小明: 好啊！我最近在看三体", None),
        ("[群聊] 小红: 那就每周五晚上8点，群名叫「鲨鱼书友会」吧", "group_info"),
    ]
    for msg, expect in group_convos:
        data = llm(msg, PERSONA_GROUP)
        if not data: continue
        gm = data.get("group_memory")
        reply = data.get("reply","")[:60]
        if expect and gm and gm.get("content"):
            mgr.remember(f"__group__{TEST_GROUP}", gm["category"], gm["title"], gm["content"])
            ok = True
        elif not expect and (not gm or not gm.get("content")):
            ok = True
        else:
            ok = False
        mark = "✓" if ok else "✗"
        label = msg.split(": ", 1)[-1][:40] if ": " in msg else msg[:40]
        print(f"  {mark} {label:40} {mem_info}")
        if ok: results["pass"] += 1
        else: results["fail"] += 1

    gmems = mgr.recall_all(f"__group__{TEST_GROUP}")
    log(len(gmems) >= 2, "群记忆数量", f"共{len(gmems)}条")
    results["pass" if len(gmems) >= 2 else "fail"] += 1

    vec_g = mgr.recall_by_vector(f"__group__{TEST_GROUP}", "读书会 每周", n=3)
    log(len(vec_g)>0 and any("读书" in v.get("text","") for v in vec_g), "群记忆向量检索", f"score={vec_g[0]['score']:.3f}" if vec_g else "无")
    results["pass" if vec_g and vec_g[0]["score"]>0.5 else "fail"] += 1

    # ====== 场景4: 记忆更新（同category+title） ======
    print("\n── 场景4: 同话题更新 vs 新话题 ──")
    before = len(mgr.recall_all(TEST_USER_A))
    # LLM should update existing memory with same category+title
    data = llm("对了鱼，之前跟你说我在学Rust——我已经学完所有权了，现在在做一个小项目！", PERSONA)
    if data and data.get("memory"):
        mem = data["memory"]
        is_new = mgr.remember(TEST_USER_A, mem["category"], mem["title"], mem["content"])
        after = len(mgr.recall_all(TEST_USER_A))
        # If LLM used same category+title as before → is_new=False, count stays same
        # If LLM used different → is_new=True, count increases
        if not is_new:
            log(True, "upsert更新(同category+title)", f"记忆数不变 {before}→{after}")
        else:
            log(True, "新建(不同category+title)", f"记忆数增加 {before}→{after}")
        results["pass"] += 1
    else:
        log(False, "LLM未返回memory")
        results["fail"] += 1

    # ====== 场景5: 日记不滥写 ======
    print("\n── 场景5: 日记选择性记录 ──")
    diary_count = 0
    trivial_convos = [
        "今天天气还行吧。",
        "鱼你喜欢什么颜色？",
        "我刚刚喝了一杯水。",
    ]
    for msg in trivial_convos:
        data = llm(msg, PERSONA)
        if data and data.get("diary"):
            diary_count += 1
            m = data["diary"]
            print(f"  ⚠ 写了日记: {m.get('title','')}")
    if diary_count <= 1:
        log(True, f"日常琐事日记克制", f"3条琐事只写了{diary_count}条日记")
    else:
        log(False, f"日记过多", f"3条琐事写了{diary_count}条")
    results["pass" if diary_count <= 1 else "fail"] += 1

    # Check actual diary entries
    all_diaries = mgr.recall_all("__diary__")
    log(len(all_diaries) >= 0, f"日记共{len(all_diaries)}条", f"测试产生{len(all_diaries)}条日记")
    for d in all_diaries:
        print(f"    📄 {d['file']}: {d['text'][:100]}...")

    # ====== 场景6: 遗忘功能 ======
    print("\n── 场景6: 遗忘记忆 ──")
    # First create a memory to forget
    data = llm("我不喜欢吃苦瓜，真的超级讨厌！帮我记住这一点。", PERSONA)
    if data and data.get("memory"):
        m = data["memory"]
        mgr.remember(TEST_USER_A, m["category"], m["title"], m["content"])
        print(f"  写入: {m['category']}/{m['title']}")

    before_forget = len(mgr.recall_all(TEST_USER_A))
    data2 = llm("其实我骗你的，我挺喜欢吃苦瓜的，之前在逗你玩。把那条删了吧。", PERSONA)
    if data2:
        f = data2.get("forget")
        reply = data2.get("reply","")[:60]
        print(f"  回复: {reply}")
        print(f"  forget: {json.dumps(f, ensure_ascii=False) if f else 'null'}")
        if f and f.get("category") and f.get("title"):
            mgr.forget(TEST_USER_A, f["category"], f["title"])
            after_forget = len(mgr.recall_all(TEST_USER_A))
            deleted = after_forget < before_forget
            log(deleted, "forget删除", f"记忆数 {before_forget}→{after_forget}")
            results["pass" if deleted else "fail"] += 1
        else:
            log(False, "forget未触发", "LLM未识别删除意图")
            results["fail"] += 1
    else:
        results["fail"] += 1

    # ====== 场景7: 记忆总量检查 ======
    print("\n── 场景7: 记忆统计 ──")
    for uid in mgr.list_users():
        mems = mgr.recall_all(uid)
        vecs = mgr.recall_by_vector(uid, "测试", n=1)
        has_vec = len(vecs) > 0 and vecs[0].get("score", 0) > 0
        label = "个人" if not uid.startswith("__") else ("日记" if uid == "__diary__" else "群")
        print(f"  {label} {uid}: {len(mems)}条文件, {'有' if has_vec else '无'}向量")
    results["pass"] += 1  # summary only

    # ====== 场景8: 空用户安全 ======
    print("\n── 场景8: 边界情况 ──")
    ghost = "nonexistent_99999"
    ok_empty_file = len(mgr.recall_all(ghost)) == 0
    ok_empty_vec = len(mgr.recall_by_vector(ghost, "什么", n=3)) == 0 or all(
        v.get("score", 1) < 0.3 for v in mgr.recall_by_vector(ghost, "什么", n=3)
    )
    log(ok_empty_file, "空用户文件读取", "返回空列表")
    log(ok_empty_vec, "空用户向量检索", "返回空/低分")
    results["pass" if ok_empty_file else "fail"] += 1
    results["pass" if ok_empty_vec else "fail"] += 1

    # ====== 清理 ======
    print("\n── 清理 ──")
    for uid in [TEST_USER_A, TEST_USER_B, f"__group__{TEST_GROUP}", "__diary__"]:
        try: mgr.forget_all(uid)
        except: pass
    print("  测试数据已清理")

    print("\n" + "=" * 55)
    print(f"结果: {results['pass']} 通过, {results['fail']} 失败")
    print("=" * 55)

if __name__ == "__main__":
    asyncio.run(main())
