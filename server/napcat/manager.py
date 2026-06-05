"""
NapCatQQ 进程管理器 — 使用 Docker 运行 NapCatQQ 实例。
每个 QQ 号一个独立容器，配置通过 volume 挂载。
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import httpx

from server.config import DATA_DIR

logger = logging.getLogger("dudushark.napcat")

NAPCLIENTS_DIR = DATA_DIR / "napcat_instances"
NAPCAT_IMAGE = "napneko/napcat"


class NapCatInstance:
    """单个 NapCatQQ Docker 实例。"""

    def __init__(self, qq: str, napcat_path: str = None):
        self.qq = qq
        self.inst_dir = NAPCLIENTS_DIR / qq
        self.ws_port = 8080
        self.webui_port = 6099
        self.container_name = f"napcat_{qq}"

    def ensure_config(self) -> bool:
        """生成 NapCatQQ 的 OneBot 配置文件。"""
        config_dir = self.inst_dir / "config"
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
                        # Docker 内访问宿主机用 host.docker.internal
                        "url": f"ws://host.docker.internal:{self.ws_port}/onebot/v11/ws/{self.qq}",
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

        webui_config = {"host": "0.0.0.0", "port": 6099, "token": "", "loginRate": 3}
        webui_path = config_dir / "webui.json"
        webui_path.write_text(json.dumps(webui_config, indent=2, ensure_ascii=False))

        return True

    async def start(self) -> bool:
        """启动 NapCatQQ Docker 容器。"""
        if not shutil.which("docker"):
            logger.error(f"[{self.qq}] 未安装 Docker，请先安装 Docker。")
            return False

        self.ensure_config()

        # 如果容器已存在但未运行，先删除
        try:
            result = subprocess.run(
                ["docker", "inspect", self.container_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                subprocess.run(["docker", "rm", "-f", self.container_name],
                               capture_output=True)
        except Exception:
            pass

        config_dir = str(self.inst_dir / "config")
        cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "-p", f"{self.webui_port}:6099",
            "-v", f"{config_dir}:/app/config",
            "--restart", "unless-stopped",
            NAPCAT_IMAGE,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info(f"[{self.qq}] NapCatQQ Docker 容器已启动 "
                            f"(WebUI: http://127.0.0.1:{self.webui_port}/webui/)")
                asyncio.create_task(self._stream_logs())
                return True
            else:
                logger.error(f"[{self.qq}] Docker 启动失败: {result.stderr.strip()}")
                return False
        except Exception as e:
            logger.error(f"[{self.qq}] 启动失败: {e}")
            return False

    async def _stream_logs(self):
        """读取容器日志。"""
        proc = await asyncio.create_subprocess_exec(
            "docker", "logs", "-f", "--since", "1s", self.container_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            while proc.returncode is None:
                line = await proc.stdout.readline()
                if not line:
                    break
                logger.info(f"[NapCat:{self.qq}] {line.decode().rstrip()}")
        except asyncio.CancelledError:
            proc.kill()
        except Exception:
            pass

    async def stop(self):
        """停止并删除 NapCatQQ Docker 容器。"""
        try:
            subprocess.run(["docker", "stop", self.container_name],
                           capture_output=True, timeout=15)
            subprocess.run(["docker", "rm", self.container_name],
                           capture_output=True)
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}",
                 self.container_name],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() == "true"
        except Exception:
            return False

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
