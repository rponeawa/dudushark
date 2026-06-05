"""
NapCatQQ 进程管理器 — 管理 NapCatQQ 实例的生命周期。
支持多 QQ 实例隔离。
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import httpx

from server.config import DATA_DIR

logger = logging.getLogger("dudushark.napcat")

NAPCLIENTS_DIR = DATA_DIR / "napcat_instances"


class NapCatInstance:
    """单个 NapCatQQ 实例。"""

    def __init__(self, qq: str, napcat_path: str = None):
        self.qq = qq
        self.inst_dir = NAPCLIENTS_DIR / qq
        self.napcat_path = napcat_path or self._find_napcat()
        self.process: subprocess.Popen | None = None
        self.ws_port = 8080
        self.webui_port = 6099
        self._ready = False

    def _find_napcat(self) -> str | None:
        # v4.x: napcat.mjs (Node.js), v3.x: napcat.sh
        for entry in ["napcat.mjs", "napcat.sh"]:
            for base in ["/opt/NapCatQQ", os.path.expanduser("~/NapCatQQ")]:
                p = os.path.join(base, entry)
                if os.path.exists(p):
                    return p
        for cmd in ["napcat", "napcat.sh"]:
            if shutil.which(cmd):
                return cmd
        return None

    def _napcat_home(self) -> Path:
        """NapCatQQ 配置目录。优先使用 Mac Installer 路径。"""
        for p in [
            "~/Library/Application Support/QQ/NapCat",
            "~/Library/Containers/com.tencent.qq/Data/Documents/napcat",
        ]:
            path = Path(os.path.expanduser(p))
            if path.exists():
                return path
        if self.napcat_path:
            return Path(self.napcat_path).resolve().parent
        return Path.home() / "NapCatQQ"

    def ensure_config(self) -> bool:
        """生成 NapCatQQ 的 OneBot 配置文件。"""
        config_dir = self._napcat_home() / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        onebot_config = {
            "musicSignUrl": "",
            "enableLocalFile2Url": False,
            "parseMultMsg": True,
            "network": {
                "websocketServers": [],
                "websocketClients": [
                    {
                        "name": "dudushark",
                        "enable": True,
                        "url": f"ws://127.0.0.1:{self.ws_port}/onebot/v11/ws/{self.qq}",
                        "messagePostFormat": "array",
                        "reportSelfMessage": False,
                        "reconnectInterval": 5000,
                        "token": "",
                        "debug": False,
                        "heartInterval": 30000,
                    }
                ],
            },
        }
        config_path = config_dir / f"onebot11_{self.qq}.json"
        config_path.write_text(json.dumps(onebot_config, indent=2, ensure_ascii=False))

        napcat_config = {
            "fileLog": True,
            "consoleLog": True,
            "fileLogLevel": "info",
            "consoleLogLevel": "info",
        }
        napcat_path_cfg = config_dir / f"napcat_{self.qq}.json"
        napcat_path_cfg.write_text(json.dumps(napcat_config, indent=2, ensure_ascii=False))

        webui_config = {"host": "0.0.0.0", "port": self.webui_port, "token": "", "loginRate": 3}
        webui_path = config_dir / "webui.json"
        webui_path.write_text(json.dumps(webui_config, indent=2, ensure_ascii=False))

        return True

    def _setup_macos_env(self, env: dict[str, str], napcat_home: Path):
        """macOS: 设置环境变量让 NapCatQQ 找到沙盒版 QQ 资源。"""
        sandbox_config = os.path.expanduser(
            "~/Library/Containers/com.tencent.qq/Data/Library/Application Support/QQ/versions/config.json"
        )
        if os.path.exists(sandbox_config):
            env["NAPCAT_QQ_VERSION_CONFIG_PATH"] = sandbox_config

        pkg_json = napcat_home / "qq_package.json"
        if pkg_json.exists():
            env["NAPCAT_QQ_PACKAGE_INFO_PATH"] = str(pkg_json)

        wrapper_node = "/Applications/QQ.app/Contents/Resources/app/wrapper.node"
        if os.path.exists(wrapper_node):
            env["NAPCAT_WRAPPER_PATH"] = wrapper_node

    async def start(self) -> bool:
        """启动 NapCatQQ 实例。NapCat-Mac-Installer 模式只需重启 QQ。"""
        self.ensure_config()

        # macOS NapCat-Mac-Installer：写配置后重启 QQ 即可加载 NapCat
        if sys.platform == "darwin" and os.path.exists("/Applications/QQ.app"):
            subprocess.run(["pkill", "-x", "QQ"], capture_output=True)
            await asyncio.sleep(2)
            subprocess.Popen(["/Applications/QQ.app/Contents/MacOS/QQ", "--no-sandbox"])
            await asyncio.sleep(6)
            logger.info(f"[{self.qq}] QQ 已重启，NapCat 加载中")
            self.process = True
            return True

        # 通用路径：运行 node napcat.mjs
        if not self.napcat_path:
            logger.error(f"[{self.qq}] 未找到 NapCatQQ。")
            return False

        napcat_home = self._napcat_home()
        env = os.environ.copy()

        try:
            if self.napcat_path.endswith(".mjs"):
                if not shutil.which("node"):
                    logger.error(f"[{self.qq}] 需要 Node.js。")
                    return False
                cmd = ["node", self.napcat_path, "-q", self.qq]
            elif self.napcat_path.endswith(".sh"):
                cmd = ["bash", self.napcat_path, "-q", self.qq]
            else:
                cmd = [self.napcat_path, "-q", self.qq]

            self.process = subprocess.Popen(
                cmd, env=env, cwd=str(napcat_home),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                preexec_fn=os.setsid if sys.platform != "win32" else None,
            )
            asyncio.create_task(self._read_output())
            logger.info(f"[{self.qq}] NapCatQQ 已启动 (PID: {self.process.pid})")
            return True
        except Exception as e:
            logger.error(f"[{self.qq}] 启动失败: {e}")
            return False

    async def _check_startup(self):
        await asyncio.sleep(5)
        if not self.is_running and self.process:
            logger.error(
                f"[{self.qq}] NapCatQQ 进程启动后立即退出。"
                "macOS 请使用官网 QQ (im.qq.com) 而非 App Store 版，或使用 Docker。"
            )

    async def _read_output(self):
        """读取 NapCatQQ 进程输出。"""
        loop = asyncio.get_event_loop()
        while self.process and self.process.poll() is None:
            try:
                line = await loop.run_in_executor(None, self.process.stdout.readline)
                if not line:
                    break
                logger.info(f"[NapCat:{self.qq}] {line.rstrip()}")
            except Exception:
                break

    async def stop(self):
        """停止 NapCatQQ 实例。"""
        if self.process:
            try:
                if sys.platform == "win32":
                    self.process.terminate()
                else:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=10)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    @property
    def is_running(self) -> bool:
        if self.process is True:
            return True
        return self.process is not None and self.process.poll() is None

    async def get_qr_code(self) -> str | None:
        """尝试通过 NapCatQQ WebUI API 获取二维码。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"http://127.0.0.1:{self.webui_port}/api/qrcode"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("qrcode") or data.get("data", {}).get("qrcode")
        except Exception:
            pass
        return None


class NapCatManager:
    """管理所有 NapCatQQ 实例。"""

    def __init__(self):
        self.instances: dict[str, NapCatInstance] = {}

    def create(self, qq: str, napcat_path: str = None) -> NapCatInstance:
        inst = NapCatInstance(qq, napcat_path)
        self.instances[qq] = inst
        return inst

    def get(self, qq: str) -> NapCatInstance | None:
        return self.instances.get(qq)

    def remove(self, qq: str):
        self.instances.pop(qq, None)

    def list_instances(self) -> list[str]:
        return list(self.instances.keys())

    async def stop_all(self):
        for inst in self.instances.values():
            await inst.stop()
        self.instances.clear()


napcat_manager = NapCatManager()
