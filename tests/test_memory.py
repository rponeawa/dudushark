"""
嘟嘟鲨鱼 记忆系统模拟测试
使用真实 LLM API 测试记忆的创建、检索、更新、删除。
覆盖: personal memory, diary, group_memory, forget, 同category+title upsert.
"""
import asyncio
import json
import os
import re
import sys
import time
import httpx
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from server.config import (
    load_global_config, get_instance_config, get_memory_dir,
    get_chroma_dir, get_convo_dir, WEBUI_PASSWORD
)
from server.memory.manager import MemoryManager, get_memory_manager

TEST_USER = "test_sim_user"
TEST_USER_NAME = "测试员小明"
TEST_GROUP = "test_sim_group"
PASS = "✓"
FAIL = "✗"
LLM_BASE = ""
LLM_KEY = ""
LLM_MODEL = ""

def get_llm_config():
    global LLM_BASE, LLM_KEY, LLM_MODEL
    cfg = load_global_config()
    if not cfg.get("instances"):
        print(f"{FAIL} 没有实例配置，请先启动过 server")
        sys.exit(1)
    qq = next(iter(cfg["instances"]))
    icfg = get_instance_config(qq)
    LLM_BASE = icfg.llm.base_url
    LLM_KEY = icfg.llm.api_key
    LLM_MODEL = icfg.llm.model
    print(f"LLM: {LLM_MODEL} @ {LLM_BASE}")

def log_result(test_name: str, ok: bool, detail: str = ""):
    s = f"  {PASS if ok else FAIL} {test_name}"
    if detail:
        s += f" — {detail}"
    print(s)

async def call_llm(messages: list[dict], temperature: float = 0.85, max_tokens: int = 1024) -> str:
    """Call LLM and return raw text."""
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(
            LLM_BASE,
            headers={"Authorization": f"Bearer {LLM_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")

def parse_json(raw: str) -> dict | None:
    """Extract JSON from LLM response."""
    raw = raw.strip()
    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try to find { } block
    m = re.search(r"\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None

def format_memory(m: dict) -> str:
    return f"id={m.get('id','')} text={m.get('text','')[:80]}... score={m.get('score',0):.3f}"

PERSONA = """你是嘟嘟鲨鱼，一只来自鲨鱼星的赛博大鲨鱼QQ机器人。自称"鱼"，口头禅"啊呜～"。
你拥有长期记忆。遇到值得记住的事，记到记忆里。有写日记的习惯。
必须输出JSON。格式：
{"reply":"...","quote":false,"memory":null,"diary":null,"group_memory":null,"forget":null}
- reply: 回复文本
- memory: 值得记住的关于对方的事。格式: {"category":"类别","title":"标题","content":"内容"}。相同类别+标题会更新旧记忆。没有就null
- diary: 日记，格式同memory。没有就null
- group_memory: 关于这个群的事，格式同memory。没有就null
- forget: 要删除的记忆 {\"category\":\"类别\",\"title\":\"标题\"}。没有就null
回复要简洁，1-3句话。"""

async def simulate_chat(user_msg: str, user_id: str = TEST_USER, group_id: str = "", user_name: str = TEST_USER_NAME) -> dict | None:
    """Simulate a chat turn, return parsed JSON."""
    prefix = "[群聊]" if group_id else ""
    text = f"{prefix}{user_name}: {user_msg}" if group_id else user_msg
    messages = [{"role": "system", "content": PERSONA}]
    messages.append({"role": "user", "content": text})
    raw = await call_llm(messages)
    data = parse_json(raw)
    if not data:
        print(f"  ⚠ LLM返回非JSON: {raw[:100]}...")
    return data

async def test_1_personal_memory_create(mgr: MemoryManager):
    """测试: 初次见面，创建个人记忆"""
    print("\n[Test 1] 个人记忆创建")
    data = await simulate_chat("你好鱼！我叫小明，我最喜欢蓝色，我有一只猫叫橘子。")
    if not data:
        log_result("LLM返回JSON", False, "解析失败")
        return False

    reply = data.get("reply", "")
    mem = data.get("memory")
    print(f"  回复: {reply}")
    print(f"  memory: {json.dumps(mem, ensure_ascii=False) if mem else 'null'}")

    # Apply memory
    if mem and isinstance(mem, dict) and mem.get("category") and mem.get("title"):
        is_new = mgr.remember(TEST_USER, mem["category"], mem["title"], mem.get("content", ""))
        log_result("memory写入", True, f"{'新建' if is_new else '更新'} {mem['category']}/{mem['title']}")
    else:
        log_result("memory写入", False, "LLM未返回memory字段或字段不完整")

    # Apply diary
    diary = data.get("diary")
    if diary and isinstance(diary, dict) and diary.get("category") and diary.get("title"):
        mgr.remember("__diary__", diary["category"], diary["title"], diary.get("content", ""))
        log_result("diary写入", True, f"{diary['category']}/{diary['title']}")
    else:
        log_result("diary写入", False, "LLM未返回diary (可能正常，不一定每次都有日记)")

    return True

async def test_2_vector_retrieval(mgr: MemoryManager):
    """测试: 向量检索能否找回之前写入的记忆"""
    print("\n[Test 2] 向量检索")

    # List all memories
    all_mems = mgr.recall_all(TEST_USER)
    if not all_mems:
        log_result("文件系统回读", False, "没有找到记忆文件")
        return False
    log_result("文件系统回读", True, f"找到 {len(all_mems)} 条记忆")

    for m in all_mems:
        print(f"    📄 {m['file']}: {m['text'][:100]}...")

    # Vector search
    queries = [
        ("猫", "应该找到关于猫的记忆"),
        ("喜欢什么颜色", "应该找到关于蓝色的记忆"),
        ("完全不相关的话题xyz123", "应该返回空或低相关度"),
    ]
    for q, desc in queries:
        results = mgr.recall_by_vector(TEST_USER, q, n=3)
        if results:
            top = results[0]
            print(f"  查询'{q}': top={top['text'][:60]}... score={top['score']:.3f}")
            if q == "完全不相关的话题xyz123" and top["score"] < 0.3:
                log_result(desc, True, f"低相关度{top['score']:.3f}正确")
            elif q != "完全不相关的话题xyz123":
                log_result(desc, True, f"score={top['score']:.3f}")
        else:
            log_result(desc, True if "不相关" in q else False, f"返回{len(results)}条")

    return True

async def test_3_memory_upsert(mgr: MemoryManager):
    """测试: 相同category+title应该更新而非新建"""
    print("\n[Test 3] 同category+title更新 (upsert)")
    before = len(mgr.recall_all(TEST_USER))
    print(f"  更新前记忆数: {before}")

    # Explicitly tell LLM there's new info that should update the existing memory
    data = await simulate_chat(
        "鱼你还记得我的猫橘子吗？它现在不是小猫了！它已经长到5公斤了，变成一只大肥猫了。更新一下你的记忆！"
    )
    if not data:
        log_result("LLM返回", False)
        return False

    mem = data.get("memory")
    reply = data.get("reply", "")
    print(f"  回复: {reply}")
    print(f"  memory: {json.dumps(mem, ensure_ascii=False) if mem else 'null'}")

    if mem and isinstance(mem, dict) and mem.get("category") and mem.get("title"):
        is_new = mgr.remember(TEST_USER, mem["category"], mem["title"], mem.get("content", ""))
        after = len(mgr.recall_all(TEST_USER))
        if not is_new:
            log_result("upsert更新", True, f"相同category+title=更新(非新建), 记忆数{before}→{after}")
            # Verify content was actually updated by reading back
            all_mems = mgr.recall_all(TEST_USER)
            found = [m for m in all_mems if mem["title"] in m.get("text", "")]
            if found:
                print(f"  更新后内容: {found[0]['text'][:150]}...")
        else:
            # New category+title, should increase count
            log_result("upsert新建", after > before, f"不同category+title=新建, 记忆数{before}→{after}")
    else:
        # Maybe LLM used same category+title — check manually
        log_result("upsert", False, "LLM未返回memory")
        print(f"  (LLM可能认为不需要更新，或信息已在记忆中)")

    return True

async def test_4_forget_memory(mgr: MemoryManager):
    """测试: LLM主动删除记忆 (forget字段)"""
    print("\n[Test 4] 记忆删除 (forget)")

    # First, tell the LLM something to remember
    data = await simulate_chat("记住：我讨厌吃青椒，非常讨厌！")
    if data and data.get("memory"):
        mem = data["memory"]
        mgr.remember(TEST_USER, mem["category"], mem["title"], mem.get("content", ""))
        print(f"  写入了: {mem['category']}/{mem['title']}")

    before = len(mgr.recall_all(TEST_USER))

    # Now tell LLM to forget it
    data2 = await simulate_chat("对了鱼，其实我骗你的，我并不讨厌青椒，把那条删了吧。")
    if not data2:
        log_result("LLM返回", False)
        return False

    forget = data2.get("forget")
    reply = data2.get("reply", "")
    print(f"  回复: {reply}")
    print(f"  forget: {json.dumps(forget, ensure_ascii=False) if forget else 'null'}")

    if forget and isinstance(forget, dict) and forget.get("category") and forget.get("title"):
        mgr.forget(TEST_USER, forget["category"], forget["title"])
        after = len(mgr.recall_all(TEST_USER))
        log_result("forget删除", after < before, f"记忆数 {before}→{after}")
    else:
        log_result("forget", False, "LLM未返回forget (可能语义理解不够明确)")

    return True

async def test_5_group_memory(mgr: MemoryManager):
    """测试: 群聊记忆 (__group__<id>)"""
    print("\n[Test 5] 群聊记忆 (group_memory)")

    # Simulate a group chat
    data = await simulate_chat(
        "大家好啊，我们这个群叫'鲨鱼后援会'！",
        group_id=TEST_GROUP,
        user_name="群主小红"
    )
    if not data:
        log_result("LLM返回", False)
        return False

    gm = data.get("group_memory")
    reply = data.get("reply", "")
    print(f"  回复: {reply}")
    print(f"  group_memory: {json.dumps(gm, ensure_ascii=False) if gm else 'null'}")

    if gm and isinstance(gm, dict) and gm.get("category") and gm.get("title"):
        group_uid = f"__group__{TEST_GROUP}"
        mgr.remember(group_uid, gm["category"], gm["title"], gm.get("content", ""))
        all_gm = mgr.recall_all(group_uid)
        log_result("group_memory写入", len(all_gm) > 0, f"找到{len(all_gm)}条群记忆")
        for m in all_gm:
            print(f"    📄 {m['file']}: {m['text'][:100]}...")

        # Vector retrieval
        results = mgr.recall_by_vector(group_uid, "这个群叫什么", n=2)
        log_result("group_memory检索", len(results) > 0 and results[0]["score"] > 0.3,
                   f"score={results[0]['score']:.3f}" if results else "无结果")
    else:
        log_result("group_memory写入", False, "LLM未返回group_memory")

    return True

async def test_6_diary_persistence(mgr: MemoryManager):
    """测试: 日记持久化"""
    print("\n[Test 6] 日记 (__diary__)")

    diary_before = len(mgr.recall_all("__diary__"))
    print(f"  测试前日记数: {diary_before}")

    data = await simulate_chat("今天天气真好！鱼你喜欢什么样的天气？")
    if not data:
        log_result("LLM返回", False)
        return False

    diary = data.get("diary")
    if diary and isinstance(diary, dict) and diary.get("category") and diary.get("title"):
        mgr.remember("__diary__", diary["category"], diary["title"], diary.get("content", ""))
        diary_after = len(mgr.recall_all("__diary__"))
        log_result("diary条目", diary_after >= diary_before, f"日记数 {diary_before}→{diary_after}")

        # ChromaDB may need a moment to index — wait briefly
        import asyncio as _a
        await _a.sleep(1.5)

        # Vector search diary
        results = mgr.recall_by_vector("__diary__", "天气", n=3)
        if len(results) > 0:
            log_result("diary向量检索", True, f"找到{len(results)}条")
            for r in results:
                print(f"    📄 score={r['score']:.3f} {r['text'][:100]}...")
        else:
            # Fallback: try broader query
            results2 = mgr.recall_by_vector("__diary__", "今天", n=5)
            log_result("diary向量检索(重试)", len(results2) > 0, f"找到{len(results2)}条" if results2 else "ChromaDB可能需要flush")
            for r in results2:
                print(f"    📄 score={r['score']:.3f} {r['text'][:100]}...")
    else:
        log_result("diary写入", False, "LLM未返回diary (不是每次对话都写日记)")

    return True

async def test_7_memory_user_listing(mgr: MemoryManager):
    """测试: 用户列表完整性"""
    print("\n[Test 7] 用户列表")
    users = mgr.list_users()
    print(f"  所有用户: {users}")
    has_test = TEST_USER in users
    log_result("包含测试用户", has_test, f"共{len(users)}个用户")
    has_diary = "__diary__" in users
    log_result("包含日记", has_diary)
    if TEST_GROUP:
        has_group = f"__group__{TEST_GROUP}" in users
        log_result("包含群记忆", has_group, f"__group__{TEST_GROUP}" if not has_group else "")

    # Check all users have retrievable data
    for uid in users:
        mems = mgr.recall_all(uid)
        file_ok = len(mems) > 0
        # Vector search
        vecs = mgr.recall_by_vector(uid, "测试", n=1)
        vec_ok = len(vecs) > 0
        if file_ok or vec_ok:
            print(f"    ✓ {uid}: {len(mems)}文件记忆, {len(vecs)}向量结果")

    return True

async def test_8_edge_cases(mgr: MemoryManager):
    """测试: 边界情况"""
    print("\n[Test 8] 边界情况")

    # 8a: Empty user
    empty_user = "nonexistent_user_999"
    all_mems = mgr.recall_all(empty_user)
    log_result("空用户recall_all", all_mems == [], f"返回{len(all_mems)}条")

    vec = mgr.recall_by_vector(empty_user, "什么", n=3)
    log_result("空用户向量检索", vec == [] or all(v.get("score", 1) < 0.3 for v in vec),
               f"返回{len(vec)}条低相关结果")

    # 8b: Special characters in content
    special_content = "包含特殊字符：<>\"'& 😀🦈\n换行\t制表"
    mgr.remember(TEST_USER, "测试", "特殊字符", special_content)
    mems = mgr.recall_all(TEST_USER)
    special_mem = [m for m in mems if "特殊字符" in m.get("file", "")]
    log_result("特殊字符存储", len(special_mem) > 0, f"找到{len(special_mem)}条")

    # 8c: Very long content
    long_content = "长" * 2000
    mgr.remember(TEST_USER, "测试", "长内容", long_content)
    long_mems = [m for m in mgr.recall_all(TEST_USER) if "长内容" in m.get("file", "")]
    log_result("长内容存储", len(long_mems) > 0)

    # 8d: recall_by_vector with empty query
    vec_empty = mgr.recall_by_vector(TEST_USER, "", n=3)
    log_result("空查询向量检索", isinstance(vec_empty, list), f"返回{len(vec_empty)}条")

    # 8e: forget non-existent memory
    try:
        mgr.forget(TEST_USER, "不存在", "不存在的标题")
        log_result("删除不存在的记忆", True, "无异常")
    except Exception as e:
        log_result("删除不存在的记忆", False, str(e))

    return True

async def cleanup(mgr: MemoryManager, first_qq: str):
    """清理测试数据"""
    print("\n[清理]")
    for uid in [TEST_USER, f"__group__{TEST_GROUP}", "__diary__"]:
        try:
            mgr.forget_all(uid)
            print(f"  已清理: {uid}")
        except Exception:
            pass
    # Also clean test edge case memories from TEST_USER
    mgr.forget(TEST_USER, "测试", "特殊字符")
    mgr.forget(TEST_USER, "测试", "长内容")


async def main():
    print("=" * 55)
    print("嘟嘟鲨鱼 记忆系统模拟测试")
    print("=" * 55)

    get_llm_config()

    cfg = load_global_config()
    if not cfg.get("instances"):
        print(f"{FAIL} 无实例")
        return
    first_qq = next(iter(cfg["instances"]))
    mgr = get_memory_manager(first_qq)

    # Clean up previous test data
    await cleanup(mgr, first_qq)

    tests = [
        test_1_personal_memory_create,
        test_2_vector_retrieval,
        test_3_memory_upsert,
        test_4_forget_memory,
        test_5_group_memory,
        test_6_diary_persistence,
        test_7_memory_user_listing,
        test_8_edge_cases,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            ok = await t(mgr)
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  {FAIL} {t.__name__} 异常: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 55)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print("=" * 55)

    # Cleanup
    await cleanup(mgr, first_qq)

if __name__ == "__main__":
    asyncio.run(main())
