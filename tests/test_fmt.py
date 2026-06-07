import asyncio, json, sys, re
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm_msg
from server.config import get_instance_config
cfg = get_instance_config('1336890338')
llm = cfg.llm

SYS = '你是嘟嘟鲨鱼。输出JSON: {"reply":"...","memory":null,"diary":null,"group_memory":null}\n- memory: 记住某个人的事。格式必须为: {"user":"名字","category":"类别","title":"标题","content":"内容"}，不能是纯文本\n- group_memory: 群整体的事。格式必须为: {"category":"类别","title":"标题","content":"内容"}，不能是纯文本\n- diary: 鱼自己的经历或感悟。格式同memory。如：被人夸了、学到了新东西、经历了特别的事、别人对鱼说了重要的话'

tests = [
    ('mem格式', '[1] 小明: 我昨天去面试了腾讯，过了'),
    ('diary被夸', '[1] 小明: 鱼你真聪明啊'),
    ('group格式', '[1] 小红: 咱们群破200人了'),
    ('diary学到', '[1] 小明: 原来Linux.do是熊猫站长'),
]

async def main():
    for label, msg in tests:
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
        raw = re.sub(r'^```json\s*|```$', '', c.strip(), flags=re.IGNORECASE).strip()
        try:
            d = json.loads(raw)
        except Exception:
            print(f'{label}: RAW={c[:300]}')
            continue
        mem = d.get('memory')
        grp = d.get('group_memory')
        dia = d.get('diary')
        ms = "OK dict" if isinstance(mem, dict) else repr(mem)[:40] if mem else "null"
        gs = "OK dict" if isinstance(grp, dict) else repr(grp)[:40] if grp else "null"
        ds = "OK dict" if isinstance(dia, dict) else repr(dia)[:40] if dia else "null"
        print(f'{label}: mem={ms} | group={gs} | diary={ds}')

asyncio.run(main())
