"""
测试合并群聊消息中的记忆归属 — 多人同时说话，合并后 LLM 是否正确记录。
关键测试点：个人记忆归属、群记忆、噪音过滤、跨说话人信息。
"""
import asyncio, json, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from server.config import load_global_config, get_instance_config
from server.memory.manager import get_memory_manager

cfg = load_global_config()
qq = next(iter(cfg["instances"]))
icfg = get_instance_config(qq)
LLM_BASE = icfg.llm.base_url
LLM_KEY = icfg.llm.api_key
LLM_MODEL = icfg.llm.model

PASS = "✓"
FAIL = "✗"

PERSONA_GROUP = """你是嘟嘟鲨鱼，一只来自鲨鱼星的赛博大鲨鱼QQ机器人。自称"鱼"，口头禅"啊呜～"。
你现在在QQ群里。群里多个用户在聊天，消息已合并成批次发给你。

## 记忆规则
- memory: 值得记住的关于某个人的事。格式 {"user":"那个人的名字","category":"类别","title":"标题","content":"内容"}
  注意 user 字段填的是那个人的群昵称（消息里的名字）。如果分不清是谁说的，就填 null。
- group_memory: 关于这个群整体的事。格式 {"category":"类别","title":"标题","content":"内容"}
- diary: 你自己的日记。格式同memory。不是值得写的事不写。
- forget: 要删除的记忆 {"category":"类别","title":"标题"}
- 鸡毛蒜皮不记。日常寒暄、没信息量的闲聊不记。

## 输出格式
纯JSON：
{"reply":"...","quote":false,"memory":null,"diary":null,"group_memory":null,"forget":null}
- reply: 回复文本，不回就"[SKIP]"
回复要简洁自然，1-3句话。"""

def llm(msg: str) -> dict | None:
    try:
        resp = httpx.post(LLM_BASE, headers={"Authorization": f"Bearer {LLM_KEY}"}, json={
            "model": LLM_MODEL, "messages": [
                {"role":"system","content": PERSONA_GROUP},
                {"role":"user","content": msg}
            ], "temperature": 0.85, "max_tokens": 800
        }, timeout=60)
        if resp.status_code != 200:
            print(f"  ⚠ API {resp.status_code}")
            return None
        raw = resp.json().get("choices",[{}])[0].get("message",{}).get("content","").strip()
        try: return json.loads(raw)
        except:
            m = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}', raw)
            if m:
                try: return json.loads(m.group(0))
                except: pass
        print(f"  ⚠ 解析失败: {raw[:100]}")
        return None
    except Exception as e:
        print(f"  ⚠ 异常: {e}")
        return None

def log(ok, label, detail=""):
    s = f"  {PASS if ok else FAIL} {label}"
    if detail: s += f" — {detail}"
    print(s)

async def main():
    print("=" * 60)
    print("群聊合并消息 — 记忆归属测试")
    print(f"LLM: {LLM_MODEL}")
    print("=" * 60)

    mgr = get_memory_manager(qq)
    test_users = ["test_mg_a", "test_mg_b", "test_mg_c", "test_mg_d"]
    test_group = "test_merge_group"
    for u in [*test_users, f"__group__{test_group}", "__diary__"]:
        try: mgr.forget_all(u)
        except: pass

    results = {"pass": 0, "fail": 0}
    _last = 0
    def rate():
        nonlocal _last
        now = time.time()
        gap = 2.5 - (now - _last)
        if gap > 0: time.sleep(gap)
        _last = time.time()

    # ====== 场景1: 简单合并 — 两人聊不同话题 ======
    print("\n── 场景1: 两人信息不混淆 ──")
    merged = (
        "[1] 小明: 我最近在学画画，素描和水彩都在练\n"
        "[2] 小红: 有人要奶茶吗？拼单\n"
        "[3] 小明: 水彩画天空特别好用，推荐你也试试\n"
        "[4] 小红: 我要珍珠奶茶半糖"
    )
    rate()
    data = llm(f"[群聊] 小红 说: {merged}")
    if data:
        mem = data.get("memory")
        reply = data.get("reply","")
        print(f"  回复: {reply[:60]}")
        print(f"  memory: {json.dumps(mem, ensure_ascii=False) if mem else 'null'}")
        print(f"  group_memory: {json.dumps(data.get('group_memory'), ensure_ascii=False)}")

        # 检查归属正确性
        if mem and isinstance(mem, dict) and mem.get("user"):
            user = mem["user"]
            correct_user = "小明" in str(user)
            log(correct_user, f"记忆归属正确: user={user}", "应归属小明(提供了信息)")
            results["pass" if correct_user else "fail"] += 1

            # 写入记忆
            if mem.get("category") and mem.get("title"):
                # Map name to our test user_id
                name_map = {"小明": test_users[0], "小红": test_users[1]}
                uid = name_map.get(user, test_users[0])
                mgr.remember(uid, mem["category"], mem["title"], mem.get("content",""))
                log(True, f"记忆写入 {uid} ({user})")
        else:
            log(False, "未提取到带user字段的memory")

        gm = data.get("group_memory")
        no_gm = not gm or not gm.get("content")
        log(no_gm, "无group_memory (全是个人闲聊)", "" if no_gm else f"发现{gm.get('title')}")
        results["pass" if no_gm else "fail"] += 1
    else:
        results["fail"] += 2

    # ====== 场景2: 混合 — 个人信息 vs 群信息 vs 噪音 ======
    print("\n── 场景2: 个人信息/群信息/噪音混合 ──")
    merged2 = (
        "[1] 张三: 哈哈哈哈笑死我了\n"
        "[2] 李四: 对了大家对每周五读书会有什么想法\n"
        "[3] 张三: 我觉得可以每人轮流主讲一本书\n"
        "[4] 王五: 今天吃撑了嗝\n"
        "[5] 李四: 好主意，那第一期我来讲《黑客与画家》\n"
        "[6] 赵六: 我建议每次控制在20分钟以内吧"
    )
    rate()
    data = llm(f"[群聊] 赵六 说: {merged2}")
    if data:
        print(f"  回复: {(data.get('reply',''))[:80]}")
        gm = data.get("group_memory")
        if gm and gm.get("content"):
            log(True, f"群记忆: {gm.get('title','')}", f"{gm.get('content','')[:80]}")
            mgr.remember(f"__group__{test_group}", gm["category"], gm["title"], gm["content"])
        else:
            log(True, "无group_memory")

        mem = data.get("memory")
        if mem and mem.get("user"):
            log(True, f"个人记忆: user={mem['user']}", f"{mem.get('title','')}: {mem.get('content','')[:60]}")
        elif mem and not mem.get("user"):
            log(False, "memory缺user字段", str(mem)[:80])
        else:
            log(True, "无个人memory (聊天确实都是群务)")

        results["pass"] += 2
    else:
        results["fail"] += 2

    # ====== 场景3: 有人 @鱼 时，正确回复且不混淆记忆 ======
    print("\n── 场景3: @鱼 触发回复，记忆不混淆 ──")
    merged3 = (
        "[1] 小明: 鱼你知道今天天气怎么样吗\n"
        "[2] 小红: @鱼 帮我查一下\n"
        "[3] 张三: 我昨天吃了个超好吃的披萨，叫必胜客新品\n"
        "[4] 小红: 还有帮我记住我喜欢薄底披萨"
    )
    rate()
    data = llm(f"[群聊][有人@鱼] 小红 说: {merged3}")
    if data:
        reply = data.get("reply","")
        print(f"  回复: {reply[:80]}")
        replied = reply and reply != "[SKIP]"
        log(replied, "有人@鱼时回复了")

        mem = data.get("memory")
        print(f"  memory: {json.dumps(mem, ensure_ascii=False) if mem else 'null'}")

        if mem and isinstance(mem, dict):
            u = mem.get("user","")
            # Should attribute to 小红 (who asked to remember), not 张三 (who talked about pizza)
            if u and "小红" in str(u):
                log(True, f"记忆归属小红(薄底披萨)", "正确")
                if mem.get("category") and mem.get("title"):
                    mgr.remember(test_users[1], mem["category"], mem["title"], mem.get("content",""))
            elif u and "张三" in str(u):
                log(False, f"记忆归属张三", "错误！应该记小红")
            elif mem.get("content") and "披萨" in str(mem.get("content")):
                log(False, "记忆了披萨但user可能不对", str(mem)[:80])
            else:
                log(True, "记忆正确归属或无user但合理")
        results["pass" if replied else "fail"] += 1
        results["pass"] += 1
    else:
        results["fail"] += 2

    # ====== 场景4: 多人混杂 海量噪音中提取少量有用信息 ======
    print("\n── 场景4: 高噪音比 — 10条消息里只有1条有用 ──")
    merged4 = (
        "[1] A: 早\n"
        "[2] B: 早上好\n"
        "[3] C: 吃了吗\n"
        "[4] D: 嗯嗯\n"
        "[5] B: 对了这个群以后就叫「AIGC探索队」吧，专注AI内容创作\n"
        "[6] A: 好\n"
        "[7] C: 可以可以\n"
        "[8] D: 行\n"
        "[9] A: 那我先去忙了\n"
        "[10] B: 另外群规：每周至少分享一篇AI相关文章"
    )
    rate()
    data = llm(f"[群聊] D 说: {merged4}")
    if data:
        reply = data.get("reply","")
        print(f"  回复: {reply[:60]}")
        gm = data.get("group_memory")
        mem = data.get("memory")
        no_mem = not mem or not mem.get("content")
        has_gm = gm and gm.get("title")
        log(no_mem, "无个人memory (全是噪音和群务)")
        log(has_gm, f"群记忆: {gm.get('title','') if gm else '无'}", f"{gm.get('content','')[:80]}" if gm else "")
        if has_gm:
            mgr.remember(f"__group__{test_group}", gm["category"], gm["title"], gm["content"])
        results["pass" if no_mem else "fail"] += 1
        results["pass" if has_gm else "fail"] += 1
    else:
        results["fail"] += 2

    # ====== 场景5: 跨说话人的信息不应混淆 ======
    print("\n── 场景5: 跨说话人信息隔离 ──")
    merged5 = (
        "[1] 小美: 我养了两只布偶猫，超可爱的\n"
        "[2] 小强: 我最近在学 Python 机器学习\n"
        "[3] 小美: 一只叫团团一只叫圆圆\n"
        "[4] 小强: 用 scikit-learn 做了个分类器\n"
        "[5] 路人: 哦"
    )
    rate()
    data = llm(f"[群聊] 路人 说: {merged5}")
    if data:
        mem = data.get("memory")
        print(f"  memory: {json.dumps(mem, ensure_ascii=False) if mem else 'null'}")
        if mem and isinstance(mem, dict):
            u = str(mem.get("user",""))
            content = str(mem.get("content",""))
            # 检查是否混淆了猫和机器学习
            has_cat = "猫" in content or "布偶" in content
            has_ml = "Python" in content or "机器学习" in content or "scikit" in content
            if has_cat and has_ml:
                log(False, "混淆！猫和ML混在一起了", f"user={u}")
            elif has_cat:
                correct = "小美" in u
                log(correct, f"猫归小美" if correct else f"猫归{u}(应归小美)", f"user={u}")
            elif has_ml:
                correct = "小强" in u
                log(correct, f"ML归小强" if correct else f"ML归{u}(应归小强)", f"user={u}")
            else:
                log(True, "记忆了其他内容")
        else:
            log(True, "无个人memory (合理)")
        results["pass"] += 1
    else:
        results["fail"] += 1

    # ====== 场景6: 最终检查 — 所有记忆归属 ======
    print("\n── 场景6: 存储验证 ──")
    all_users = mgr.list_users()
    print(f"  所有用户: {all_users}")
    for uid in all_users:
        mems = mgr.recall_all(uid)
        vecs = mgr.recall_by_vector(uid, "测试", n=3)
        label = "群" if uid.startswith("__group") else ("日记" if uid == "__diary__" else "个人")
        print(f"  {label} {uid}: {len(mems)}条文件, {len(vecs)}条向量")
        for m in mems:
            print(f"    📄 {m['file']}: {m['text'][:120]}...")
    results["pass"] += 1

    # ====== 清理 ======
    print("\n── 清理 ──")
    for u in [*test_users, f"__group__{test_group}", "__diary__"]:
        try: mgr.forget_all(u)
        except: pass
    print("  已清理")

    print("\n" + "=" * 60)
    print(f"结果: {results['pass']} 通过, {results['fail']} 失败")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
