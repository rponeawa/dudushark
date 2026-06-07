import asyncio, json, sys, re
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm, _call_llm_msg
from server.config import get_instance_config

cfg = get_instance_config('1336890338')
llm = cfg.llm

JSON_PROMPT = "【记忆规则 - 同样重要】对方说了有点意思的事就可以记。关键信息、性格、喜好、经历、约定都值得记。日常寒暄——打招呼、道晚安、随口闲聊——不是记忆。\n\n输出JSON: {\"reply\":\"...\",\"memory\":null}。memory格式: {\"user\":\"名字\",\"category\":\"类别\",\"title\":\"标题\",\"content\":\"内容\"}。"

PRE_CHECK_PROMPT = "你是记忆过滤器。判断是否值得记录。\n值得记录：关键个人信息、重要经历、性格特点、约定承诺。\n不值得记录：日常寒暄、随口闲聊、道晚安、夸两句。\n只输出 YES 或 NO。"

async def simulate(user_msg, label):
    print(f'\n=== {label} ===')
    print(f'  用户: {user_msg[:80]}')

    # Step 1: 主 LLM JSON
    msgs = [
        {'role': 'system', 'content': f'你是嘟嘟鲨鱼，傲娇的赛博大鲨鱼。{JSON_PROMPT}'},
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
        print(f'  JSON FAIL: {c[:200]}')
        return
    mem = data.get('memory')
    print(f'  reply: {data.get("reply","")[:80]}')
    print(f'  memory: {json.dumps(mem, ensure_ascii=False) if mem else "null"}')

    if not mem:
        print(f'  → 主LLM决定不记')
        return

    # Step 2: 预判
    text = f'消息：{user_msg[:200]}\n\n记忆：{mem.get("category")}/{mem.get("title")} - {mem.get("content")}'
    raw = await _call_llm(llm.base_url, llm.api_key, {'model': llm.model, 'messages': [
        {'role': 'system', 'content': PRE_CHECK_PROMPT},
        {'role': 'user', 'content': text},
    ], 'temperature': 0.1, 'max_tokens': 500}, timeout=15)
    r = raw.strip().upper()
    save = 'YES' in r and 'NO' not in r
    print(f'  pre-check: {repr(raw[:60])} → {"SAVE" if save else "SKIP"}')

async def main():
    # 应该记的
    await simulate('小明: 我下周要去日本留学了，学动画设计', '留学计划-应记')
    await simulate('小红: 我最怕打雷了，每次打雷都躲被窝里', '性格特点-应记')
    await simulate('妈妈: 嘟嘟，妈妈下个月生日哦', '重要日期-应记')
    await simulate('对方: 我最近在学Python，好难啊', '学习经历-应记')
    await simulate('小李: 我和女朋友分手了，好难过', '情感事件-应记')

    # 不应记的
    await simulate('对方: 晚安鱼', '道晚安-不记')
    await simulate('对方: 你好呀', '打招呼-不记')
    await simulate('对方: 今天天气真好', '随口闲聊-不记')
    await simulate('对方: 哈哈哈', '纯感叹-不记')

asyncio.run(main())
