import json
import os
from pathlib import Path
from pydantic import BaseModel

# Load .env file (zero-dependency, runs before env var reads)
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            if _key not in os.environ:
                os.environ[_key] = _val.strip().strip('"').strip("'")

DATA_DIR = Path(os.environ.get("DUDUSHARK_DATA", Path(__file__).parent.parent / "data"))
CONFIG_FILE = DATA_DIR / "config.json"
WEBUI_PASSWORD = os.environ.get("WEBUI_PASSWORD", "")
AUTH_ENABLED = bool(WEBUI_PASSWORD)

DEFAULT_LLM = {
    "base_url": "https://api.stepfun.com/v1/chat/completions",
    "model": "step-3.7-flash",
    "api_key": os.environ.get("STEPFUN_API_KEY", ""),
}

class LLMConfig(BaseModel):
    base_url: str = DEFAULT_LLM["base_url"]
    model: str = DEFAULT_LLM["model"]
    api_key: str = DEFAULT_LLM["api_key"]


class BotConfig(BaseModel):
    qq: str
    llm: LLMConfig = LLMConfig()
    context_max_tokens: int = 128000
    memory_retrieval_count: int = 10
    web_search_enabled: bool = True
    reply_split_enabled: bool = True
    reply_split_max: int = 5
    group_reply_ratio: float = 0.25
    private_merge_delay: float = 8.0
    group_merge_delay: float = 9.0
    napcat_webui_port: int = 6099
    onebot_ws_port: int = 8080
    # Proactive messaging
    proactive_enabled: bool = True
    proactive_global_cooldown_sec: int = 600
    proactive_per_conv_cooldown_sec: int = 2700
    proactive_group_probability: float = 0.30
    proactive_private_probability: float = 0.30
    proactive_curiosity_threshold: float = 0.35
    # TTS (语音合成)
    tts_enabled: bool = True
    tts_voice: str = "ruanmengnvsheng"
    tts_model: str = "step-tts-2"
    # TTS 语音文件在宿主机上的输出目录（需在 NapCat Docker 挂载卷内）
    tts_host_dir: str = ""
    # ASR (语音转文字)
    asr_model: str = "step-audio-2"
    asr_prompt: str = "请完整转写这段语音，一字不漏地输出说话内容，并在前面描述音色和语气。格式：\"用[音色描述]的[语气描述]声音说：[完整文字内容]\"。音色描述如：软糯少女音、低沉男声、清脆童声等。如果语音中有笑声、叹气、停顿等也描述出来。"
    # Mood system
    mood_enabled: bool = True
    # Admin list
    admins: list[dict] = []
    # 管理员角色描述（注入 system prompt，不提交到 GitHub）
    admins_description: str = ""
    # 被暂停的群聊列表
    paused_groups: list[str] = []
    # 家族记忆 + 附带指令（仅家人私聊注入，不提交到 GitHub）
    family_memory: str = ""
    family_note: str = ""


def load_global_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    cfg = {"instances": {}, "current_instance": None}
    save_global_config(cfg)
    return cfg


def save_global_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def get_instance_dir(qq: str) -> Path:
    return DATA_DIR / "instances" / qq


def get_instance_config(qq: str) -> BotConfig:
    inst_dir = get_instance_dir(qq)
    cfg_file = inst_dir / "bot_config.json"
    if cfg_file.exists():
        data = json.loads(cfg_file.read_text())
        return BotConfig(**data)
    return BotConfig(qq=qq)


def save_instance_config(cfg: BotConfig):
    inst_dir = get_instance_dir(cfg.qq)
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "bot_config.json").write_text(
        json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False)
    )


def get_memory_dir(qq: str, user_id: str) -> Path:
    p = get_instance_dir(qq) / "memories" / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_convo_dir(qq: str) -> Path:
    p = get_instance_dir(qq) / "conversations"
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_reminders_path(qq: str) -> Path:
    return get_instance_dir(qq) / "reminders.json"

def get_chroma_dir(qq: str) -> Path:
    p = get_instance_dir(qq) / "chroma"
    p.mkdir(parents=True, exist_ok=True)
    return p
