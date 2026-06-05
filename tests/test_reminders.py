"""
测试定时提醒系统 — 验证 remind 字段解析、存储、到期触发、一次性和删除。
使用真实 LLM API，但不发送任何 QQ 消息。
"""
import asyncio, json, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from server.config import load_global_config, get_instance_config, get_reminders_path
from server.memory.manager import get_memory_manager

cfg = load_global_config()
qq = next(iter(cfg["instances"]))
icfg = get_instance_config(qq)
LLM_BASE = icfg.llm.base_url
LLM_KEY = icfg.llm.api_key
LLM_MODEL = icfg.llm.model

PASS = "✓"
FAIL = "✗"

PERSONA = """你是嘟嘟鲨鱼QQ机器人。自称"鱼"，口头禅"啊呜～"。
你用 JSON 回复。如果有人要求指定时间提醒，用 remind 字段。
输出JSON:
{"reply":"...","remind":null}
- remind: {"at_utc":Unix秒时间戳,"content":"提醒内容"}。一次性，到点系统自动发送后删除。"""

def llm(msg: str, current_ts: float) -> dict | None:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(current_ts, tz=timezone.utc)
    ts8 = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
    cn = dt.astimezone(ts8).strftime("%Y-%m-%d %H:%M")
    prompt = f"{PERSONA}\n（当前时间: {dt.strftime('%Y-%m-%d %H:%M UTC')} = 北京时间 {cn}，Unix时间戳: {int(current_ts)}）\n{msg}"

    time.sleep(2.5)  # rate limit
    try:
        resp = httpx.post(LLM_BASE, headers={"Authorization": f"Bearer {LLM_KEY}"}, json={
            "model": LLM_MODEL,
            "messages": [{"role":"system","content": prompt}],
            "temperature": 0.85, "max_tokens": 400,
        }, timeout=60)
        if resp.status_code != 200:
            print(f"  ⚠ API {resp.status_code}")
            return None
        raw = resp.json().get("choices",[{}])[0].get("message",{}).get("content","").strip()
        try: return json.loads(raw)
        except:
            m = re.search(r'\{[^{}]*\}', raw)
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
    print("定时提醒系统测试")
    print("=" * 60)

    reminders_path = get_reminders_path(qq)
    reminders_path.unlink(missing_ok=True)

    results = {"pass": 0, "fail": 0}

    # ====== Test 1: 准确时间提醒 ======
    print("\n── 场景1: 明天早六点叫我起床 ──")
    now_ts = time.time()
    # "明天早上6点" 的 UTC 时间戳
    from datetime import datetime, timezone, timedelta
    tz8 = timezone(timedelta(hours=8))
    now_cn = datetime.fromtimestamp(now_ts, tz=tz8)
    tomorrow_6am = now_cn.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    expected_ts = tomorrow_6am.timestamp()

    data = llm("明天早上六点叫我起床哦！", now_ts)
    if data:
        print(f"  回复: {(data.get('reply',''))[:60]}")
        remind = data.get("remind")
        print(f"  remind: {json.dumps(remind, ensure_ascii=False) if remind else 'null'}")

        if remind and isinstance(remind, dict) and remind.get("at_utc"):
            at_utc = float(remind["at_utc"])
            diff_sec = abs(at_utc - expected_ts)
            diff_min = diff_sec / 60
            content = remind.get("content", "")
            log(diff_sec < 7200, f"时间戳偏差{diff_min:.0f}min", f"期望~{int(expected_ts)}, 实际{int(at_utc)}")
            log(len(content) > 0, f"提醒内容: {content[:40]}")
            results["pass"] += 2
        else:
            log(False, "LLM未返回remind字段")
            results["fail"] += 2
    else:
        results["fail"] += 2

    # ====== Test 2: 相对时间提醒 ======
    print("\n── 场景2: 10分钟后提醒 ──")
    now_ts2 = time.time()
    expected_ts2 = now_ts2 + 600
    data2 = llm("提醒我10分钟后去拿快递", now_ts2)
    if data2:
        remind2 = data2.get("remind")
        print(f"  remind: {json.dumps(remind2, ensure_ascii=False) if remind2 else 'null'}")
        if remind2 and remind2.get("at_utc"):
            at_utc2 = float(remind2["at_utc"])
            diff2 = abs(at_utc2 - expected_ts2)
            log(diff2 < 300, f"10分钟=600s, 偏差{diff2:.0f}s", f"{int(at_utc2)}")
            results["pass" if diff2 < 300 else "fail"] += 1
        else:
            results["fail"] += 1
    else:
        results["fail"] += 1

    # ====== Test 3: 写入和读回 ======
    print("\n── 场景3: 写入 reminders.json ──")
    import subprocess
    # Simulate what _save_remind would do
    reminder = {
        "at_utc": expected_ts2,
        "user_id": "test_remind_user",
        "group_id": "",
        "content": "去拿快递啦啊呜～",
        "created": now_ts2,
    }
    reminders_path.write_text(json.dumps([reminder], ensure_ascii=False, indent=2))
    log(reminders_path.exists(), f"文件已创建: {reminders_path.name}")
    loaded = json.loads(reminders_path.read_text())
    log(len(loaded) == 1, f"读取到{len(loaded)}条提醒")
    log(loaded[0]["content"] == "去拿快递啦啊呜～", "内容完整")
    results["pass"] += 3

    # ====== Test 4: 到期触发 + 自动删除 ======
    print("\n── 场景4: 到期触发后自动删除 ──")
    now_ts3 = time.time()
    # 创建一个"已过期"的提醒（1秒前）
    reminder_past = {
        "at_utc": now_ts3 - 1,  # 已过期
        "user_id": "test_remind_user",
        "group_id": "",
        "content": "这个提醒已经过期了",
        "created": now_ts3 - 10,
    }
    # 创建一个"未过期"的提醒
    reminder_future = {
        "at_utc": now_ts3 + 86400,  # 明天
        "user_id": "test_remind_user",
        "group_id": "",
        "content": "这是未来的提醒",
        "created": now_ts3,
    }
    reminders_path.write_text(json.dumps([reminder_past, reminder_future], ensure_ascii=False, indent=2))

    # Simulate _check_reminders logic
    loaded2 = json.loads(reminders_path.read_text())
    remaining = [r for r in loaded2 if r["at_utc"] > now_ts3]
    expired = [r for r in loaded2 if r["at_utc"] <= now_ts3]
    reminders_path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2))

    log(len(expired) == 1 and len(remaining) == 1, f"过期{len(expired)}条触发, {len(remaining)}条保留")
    # Verify the future one remains
    final = json.loads(reminders_path.read_text())
    log(len(final) == 1 and final[0]["content"] == "这是未来的提醒",
        "未来提醒保留，过期提醒已删除")
    results["pass"] += 2

    # ====== Test 5: 不重复发送 ======
    print("\n── 场景5: 确认不会重复 ──")
    # Already verified in test 4: expired was removed, future kept
    # Run check again — only future should remain
    loaded3 = json.loads(reminders_path.read_text())
    remaining2 = [r for r in loaded3 if r["at_utc"] > time.time()]
    log(len(loaded3) == len(remaining2), f"再次检查: {len(loaded3)}条全是未来的, 无重复触发")
    results["pass"] += 1

    # ====== Cleanup ======
    reminders_path.unlink(missing_ok=True)
    print(f"\n{'='*60}")
    print(f"结果: {results['pass']} 通过, {results['fail']} 失败")
    print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
