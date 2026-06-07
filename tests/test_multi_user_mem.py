import asyncio, json, sys, re
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm_msg
from server.config import get_instance_config
cfg = get_instance_config('1336890338')
llm = cfg.llm

SYS = '你是嘟嘟鲨鱼。输出JSON: {"reply":"...","memory":null}\n- memory: 记住某个人的事。格式: {"user":"名字","category":"类别","title":"标题","content":"内容"}。user填消息里的名字。'

async def main():
    # 模拟群聊合并消息，多人说话
    msg = "[1] 小明: 我昨天去面试了腾讯，过了\n[2] 小红: 恭喜恭喜！\n[3] 小明: 谢谢谢谢\n[4] 小红: 我下个月也要跳槽去字节了"
    resp = await _call_llm_msg(llm.base_url, llm.api_key, {
        'model': llm.model, 'messages': [
            {'role': 'system', 'content': SYS},
            {'role': 'user', 'content': msg},
        ], 'temperature': 0.85, 'max_tokens': 1000,
    })
    c = resp.get('content', '')
    if not c:
        r = resp.get('reasoning', '')
        m = re.search(r'\{[^{}]*"reply"[^}]*\}', r)
        c = m.group(0) if m else ''
    d = json.loads(re.sub(r'^```json\s*|```$', '', c.strip(), flags=re.IGNORECASE).strip())
    print(f'raw data: {json.dumps(d, ensure_ascii=False)[:400]}')
    mem = d.get('memory')
    if isinstance(mem, list):
        print(f'memory is LIST (multiple): {json.dumps(mem, ensure_ascii=False)[:300]}')
    elif isinstance(mem, dict):
        print(f'memory is DICT: user={mem.get("user")}')
    else:
        print(f'memory: {repr(mem)[:100]}')

    # 验证 names_map 映射
    names = ["小明", "小红", "小明", "小红"]
    user_ids = ["111", "222", "111", "222"]
    names_map = dict(zip(names, user_ids))
    if mem:
        target_user = mem.get("user")
        if target_user and target_user in names_map:
            uid = names_map[target_user]
            print(f'  names_map 映射: "{target_user}" → QQ={uid}')

asyncio.run(main())
