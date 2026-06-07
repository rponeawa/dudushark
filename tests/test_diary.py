import asyncio, json, sys, re
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm_msg
from server.config import get_instance_config

cfg = get_instance_config('1336890338')
llm = cfg.llm

SYS_G = '你是嘟嘟鲨鱼。输出JSON: {"reply":"...","memory":null,"diary":null,"group_memory":null}\n- memory: 记住某个人的事（经历、喜好、性格、约定）\n- group_memory: 关于群整体（里程碑、群活动、群氛围），非个人\n- diary: 关于鱼自己。经历了有意义的事、有了新的感悟、学到了东西、被人说了重要的话'

async def test(label, sys_prompt, user_msg):
    print(f'\n=== {label} ===')
    print(f'  msg: {user_msg[:80]}')
    resp = await _call_llm_msg(llm.base_url, llm.api_key, {'model': llm.model, 'messages': [
        {'role': 'system', 'content': sys_prompt},
        {'role': 'user', 'content': user_msg},
    ], 'temperature': 0.85, 'max_tokens': 1000})
    c = resp.get('content', '')
    if not c:
        r = resp.get('reasoning', '')
        m = re.search(r'\{[^{}]*"reply"[^}]*\}', r)
        c = m.group(0) if m else ''
    raw = re.sub(r'^```json\s*|```$', '', c.strip(), flags=re.IGNORECASE).strip()
    try:
        d = json.loads(raw)
    except Exception:
        print(f'  PARSE FAIL. raw: {c[:300]}')
        return
    for fld in ('memory', 'diary', 'group_memory'):
        val = d.get(fld)
        if val and isinstance(val, dict):
            print(f'  {fld}: {val.get("category","")}/{val.get("title","")}')
        elif val:
            print(f'  {fld}: {repr(val)[:60]} (NOT dict!)')
        else:
            print(f'  {fld}: null')

async def main():
    # diary 应触发的
    await test('群-鱼被夸', SYS_G, '[1] 小明: 鱼你真聪明啊，什么都知道')
    await test('群-鱼学到东西', SYS_G, '[1] 小红: 原来Linux.do的站长是熊猫，太厉害了')
    await test('私-被表白', SYS_G, '小明: 嘟嘟我好喜欢你啊')
    # diary 不应触发 - 这些是别人的事/群的事，不是鱼自己的
    await test('群-个人经历(应记memory)', SYS_G, '[1] 小明: 我今天去面试了腾讯')
    await test('群-群里程碑(应记group)', SYS_G, '[1] 小红: 咱们群破200人了！')

asyncio.run(main())
