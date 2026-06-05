import json
import os
from pathlib import Path
from pydantic import BaseModel

DATA_DIR = Path(os.environ.get("DUDUSHARK_DATA", Path(__file__).parent.parent / "data"))
CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_LLM = {
    "base_url": "https://api.stepfun.com/v1/chat/completions",
    "model": "step-3.5-flash-2603",
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
    private_merge_delay: float = 2.0
    group_merge_delay: float = 6.0
    napcat_webui_port: int = 6099
    onebot_ws_port: int = 8080
    # Proactive messaging
    proactive_enabled: bool = True
    proactive_global_cooldown_sec: int = 600
    proactive_per_conv_cooldown_sec: int = 2700
    proactive_group_probability: float = 0.30
    proactive_private_probability: float = 0.30
    proactive_curiosity_threshold: float = 0.35
    # Mood system
    mood_enabled: bool = True
    # Admin list
    admins: list[dict] = []


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


def get_chroma_dir(qq: str) -> Path:
    p = get_instance_dir(qq) / "chroma"
    p.mkdir(parents=True, exist_ok=True)
    return p
