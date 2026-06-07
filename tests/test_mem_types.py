import asyncio, json, sys, re
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm_msg
from server.config import get_instance_config

cfg = get_instance_config('1336890338')
llm = cfg.llm

SYS_GROUP = """你是嘟嘟鲨鱼，傲娇的赛博大鲨鱼。在群里聊天。
输出JSON: {"reply":"...","quote":false,"memory":null,"diary":null,"group_memory":null}
- memory: 关于某人的重要信息，null居多。格式: {"user":"名字","category":"类别","title":"标题","content":"内容"}。user填消息里的名字。相同类别+标题会更新
- group_memory: 关于这个群整体的事（非个人），null居多。格式: {"category":"类别","title":"标题","content":"内容"}
- diary: 你自己的全局记忆，值得写才写，null居多。格式同memory"""

SYS_PRIVATE = """你是嘟嘟鲨鱼，傲娇的赛博大鲨鱼。私聊中。
输出JSON: {"reply":"...","quote":false,"memory":null,"diary":null}
- memory: 关于对方的重要信息，null居多。格式: {"user":"名字","category":"类别","title":"标题","content":"内容"}
- diary: 你自己的全局记忆，值得写才写，null居多。格式同memory"""

async def test(label, sys_prompt, user_msg, fields_expected):
    print(f'\n=== {label} ===')
    print(f'  用户: {user_msg[:100]}')
    msgs = [
        {'role': 'system', 'content': sys_prompt},
        {'role': 'user', 'content': user_msg},
    ]
    resp = await _call_llm_msg(llm.base_url, llm.api_key, {'model': llm.model, 'messages': msgs, 'temperature': 0.85, 'max_tokens': 1000})
    c = resp.get('content', '')
    if not c:
        r = resp.get('reasoning', '')
        m = re.search(r'\{[^{}]*"reply"[^}]*\}', r)
        c = m.group(0) if m else ''
    try:
        data = json.loads(re.sub(r'^```json\s*|```$', '', c.strip(), flags=re.IGNORECASE).strip())
    except:
        print(f'  PARSE FAIL: {c[:200]}')
        return
    results = {}
    for fld in fields_expected:
        val = data.get(fld)
        if val:
            results[fld] = f'{val.get("category","")}/{val.get("title","")}'
        else:
            results[fld] = 'null'
    print(f'  memory: {results.get("memory","N/A")}')
    print(f'  group_memory: {results.get("group_memory","N/A")}')
    print(f'  diary: {results.get("diary","N/A")}')
    correct = data.get(fields_expected[0]) is not None
    print(f'  → {"OK" if correct else "ISSUE"}')

async def main():
    # 群聊场景
    print('='*60)
    print('群聊测试')
    await test('群-个人经历', SYS_GROUP, '[1] 小明: 我昨天去面试了腾讯，过了', ['memory'])
    await test('群-群整体事件', SYS_GROUP, '[1] 小明: 咱们群今天100人了！撒花', ['group_memory'])
    await test('群-鱼自己的感悟', SYS_GROUP, '[1] 小明: 鱼你今天好活跃啊', ['diary'])
    await test('群-个人喜好', SYS_GROUP, '[1] 小红: 我超级喜欢吃榴莲', ['memory'])
    await test('群-群约定', SYS_GROUP, '[1] 小红: 下周六咱们群聚会，大家都来哦', ['group_memory'])

    # 私聊场景
    print('\n' + '='*60)
    print('私聊测试')
    await test('私-个人信息', SYS_PRIVATE, '小明: 我叫小明，今年25岁，在北京工作', ['memory'])
    await test('私-鱼的感悟', SYS_PRIVATE, '小明: 鱼鱼你今天心情好像特别好？', ['diary'])
    await test('私-约定提醒', SYS_PRIVATE, '小明: 明天晚上8点提醒我开会', ['memory'])

asyncio.run(main())
