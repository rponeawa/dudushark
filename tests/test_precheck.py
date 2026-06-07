import asyncio, sys
sys.path.insert(0, '/home/hsinli/dudushark')
from server.bot.message_handler import _call_llm
from server.config import get_instance_config

cfg = get_instance_config('1336890338')
llm = cfg.llm

PROMPT = "你是记忆过滤器。判断是否值得长期记住。\n值得记录：反映人的身份背景、重要经历、深层性格、明确约定、强烈情感。\n不值得记录：一次性的随口评价（任何话题）、泛泛而谈的感受、纯情绪发泄、短暂状态的描述。\n只输出 YES 或 NO。"

tests = [
    # 应该 YES 的
    ("应-职业", "消息：我是程序员\n记忆：个人信息/职业 - 对方是程序员", True),
    ("应-家乡", "消息：我是重庆人\n记忆：个人信息/家乡 - 对方是重庆人", True),
    ("应-留学", "消息：我下月去美国留学\n记忆：经历/留学 - 去美国读计算机", True),
    ("应-约定", "消息：明晚8点见\n记忆：约定/见面 - 明天晚上8点见面", True),
    # 应该 NO 的
    ("否-随口好吃", "消息：这个炸鸡好好吃\n记忆：美食/炸鸡 - 对方觉得炸鸡好吃", False),
    ("否-随口好看", "消息：这部电影真好看\n记忆：影视/某电影 - 对方觉得某电影好看", False),
    ("否-今天好累", "消息：今天上班好累啊\n记忆：状态/疲惫 - 对方说今天上班累", False),
    ("否-哈哈笑死", "消息：哈哈哈哈哈笑死我了\n记忆：情绪/开心 - 对方笑得很开心", False),
    ("否-我在吃饭", "消息：我刚才在吃饭\n记忆：状态/吃饭 - 对方刚才在吃饭", False),
]

async def main():
    for label, text, expect_yes in tests:
        raw = await _call_llm(llm.base_url, llm.api_key, {
            'model': llm.model, 'messages': [
                {'role': 'system', 'content': PROMPT},
                {'role': 'user', 'content': text},
            ], 'temperature': 0.1, 'max_tokens': 500,
        }, timeout=15)
        r = raw.strip().upper()
        is_yes = 'YES' in r and 'NO' not in r
        status = "OK" if is_yes == expect_yes else "WRONG"
        print(f'{status} {label}: {repr(r[:60])}')

asyncio.run(main())
