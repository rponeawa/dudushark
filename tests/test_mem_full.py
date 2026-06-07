import asyncio, json, sys, re
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm, _call_llm_msg
from server.config import get_instance_config

cfg = get_instance_config('1336890338')
llm = cfg.llm

async def test_full_flow():
    # Step 1: 模拟真实的消息→LLM→解析JSON
    print('=== Step 1: 主 LLM JSON 输出 ===')
    msgs = [
        {'role': 'system', 'content': '你是嘟嘟鲨鱼。输出JSON。memory格式: {"user":"名字","category":"类别","title":"标题","content":"内容"}。对方说了重要的事（出行、约定、喜好）才记。日常闲聊不记。'},
        {'role': 'user', 'content': '妈妈: 嘟嘟，妈妈明天要去北京出差，下周三回来'},
    ]
    payload = {'model': llm.model, 'messages': msgs, 'temperature': 0.85, 'max_tokens': 2000}
    resp = await _call_llm_msg(llm.base_url, llm.api_key, payload)
    content = resp.get('content', '')
    if not content:
        r = resp.get('reasoning', '')
        m = re.search(r'\{[^{}]*"reply"[^}]*\}', r)
        content = m.group(0) if m else ''
    print(f'  content: {repr(content[:400])}')

    try:
        data = json.loads(re.sub(r'^```json\s*|```$', '', content.strip(), flags=re.IGNORECASE).strip())
    except Exception as e:
        print(f'  JSON parse failed: {e}')
        print(f'  raw content: {content[:500]}')
        return

    mem = data.get('memory')
    diary = data.get('diary')
    reply = data.get('reply', '')
    print(f'  reply: {reply[:80]}')
    print(f'  memory: {mem}')
    print(f'  diary: {diary}')

    # Step 2: 测试记忆预判
    if mem and isinstance(mem, dict):
        print('\n=== Step 2: _should_record_memory ===')
        prompt = '你是记忆过滤器。判断是否值得记录。\n值得记录：关键个人信息、重要经历、性格特点、约定承诺。\n不值得记录：日常寒暄、随口闲聊、道晚安、夸两句。\n只输出 YES 或 NO。'
        text = f'消息：妈妈明天要去北京出差\n\n记忆：{mem.get("category")}/{mem.get("title")} - {mem.get("content")}'
        payload2 = {
            'model': llm.model, 'messages': [
                {'role': 'system', 'content': prompt},
                {'role': 'user', 'content': text},
            ], 'temperature': 0.1, 'max_tokens': 500,
        }
        raw = await _call_llm(llm.base_url, llm.api_key, payload2, timeout=15)
        print(f'  raw output: {repr(raw[:200])}')
        r = raw.strip().upper()
        should_save = 'YES' in r and 'NO' not in r
        print(f'  should_save: {should_save}')
    else:
        print('\n!!! 主 LLM 没有输出 memory 字段 !!!')

    # Step 3: 更明显该记的 - 有人要出国留学
    print('\n=== Step 3: 明显的个人信息 ===')
    msgs2 = [
        {'role': 'system', 'content': '你是嘟嘟鲨鱼。输出JSON: {"reply":"...","memory":null}。memory: {"user":"名字","category":"类别","title":"标题","content":"内容"}。遇到重要个人信息、计划、约定时记录。'},
        {'role': 'user', 'content': '小明: 我下个月要去美国留学了，读计算机科学'},
    ]
    payload3 = {'model': llm.model, 'messages': msgs2, 'temperature': 0.85, 'max_tokens': 1000}
    resp2 = await _call_llm_msg(llm.base_url, llm.api_key, payload3)
    c2 = resp2.get('content', '')
    if not c2:
        r2 = resp2.get('reasoning', '')
        m = re.search(r'\{[^{}]*"reply"[^}]*\}', r2)
        c2 = m.group(0) if m else ''
    print(f'  content: {repr(c2[:400])}')
    try:
        d2 = json.loads(re.sub(r'^```json\s*|```$', '', c2.strip(), flags=re.IGNORECASE).strip())
        print(f'  reply: {d2.get("reply","")[:80]}')
        print(f'  memory: {d2.get("memory")}')
    except Exception as e:
        print(f'  parse fail: {e}')

asyncio.run(test_full_flow())
