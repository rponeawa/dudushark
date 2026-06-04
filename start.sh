#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---- 检查 ----
if ! command -v python3 &>/dev/null; then
    echo "[ERR] 请先安装 Python 3.10+"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[INFO] Python $PYVER"

# ---- 虚拟环境 ----
if [ ! -d ".venv" ]; then
    echo "[INFO] 创建虚拟环境..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# ---- Python 依赖 ----
echo "[INFO] 安装 Python 依赖..."
PIP_FLAGS="-q"
python3 -c "import sys; exit(0 if sys.version_info >= (3,14) else 1)" 2>/dev/null && PIP_FLAGS="$PIP_FLAGS --only-binary :all:"
pip install $PIP_FLAGS -r requirements.txt

# ---- 前端 ----
if command -v node &>/dev/null; then
    if [ ! -d "web/node_modules" ]; then
        echo "[INFO] 安装前端依赖..."
        (cd web && npm install)
    fi
    if [ ! -f "server/webui/static/index.html" ]; then
        echo "[INFO] 构建前端..."
        (cd web && npm run build)
    fi
fi

# ---- 加载 .env ----
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "[INFO] 加载 .env 配置..."
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ---- 启动 ----
echo "[INFO] 嘟嘟鲨鱼 启动中... 啊呜～"
export DUDUSHARK_DATA="$SCRIPT_DIR/data"
python3 -m server.main "$@"
