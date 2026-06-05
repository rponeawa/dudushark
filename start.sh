#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERR]${NC} $*"; }

# ============================================================
# 1. 环境检查
# ============================================================
if [ "$(uname -s)" != "Linux" ]; then
    err "仅支持 Linux 系统"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    err "请先安装 Python 3.10+"
    exit 1
fi
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYVER"

if ! command -v node &>/dev/null; then
    err "需要 Node.js 18+ 构建前端"
    exit 1
fi
info "Node $(node -v)"

# ============================================================
# 2. NapCatQQ (Docker)
# ============================================================
NAPCAT_CONTAINER="napcat"
NAPCAT_IMAGE="mlikiowa/napcat-docker:latest"

start_napcat_docker() {
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${NAPCAT_CONTAINER}$"; then
        info "NapCatQQ Docker 容器已在运行"
        return 0
    fi

    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${NAPCAT_CONTAINER}$"; then
        info "启动已有 NapCatQQ 容器..."
        docker start "$NAPCAT_CONTAINER"
        return 0
    fi

    info "首次启动 NapCatQQ Docker 容器..."
    mkdir -p "$HOME/NapCatQQ/config" "$HOME/NapCatQQ/plugins"

    docker run -d \
        -p 6099:6099 \
        -v "$HOME/NapCatQQ/config:/app/napcat/config" \
        -v "$HOME/NapCatQQ/plugins:/app/napcat/plugins" \
        --name "$NAPCAT_CONTAINER" \
        --restart=always \
        "$NAPCAT_IMAGE"

    info "NapCatQQ Docker 容器已创建并启动"
    info "WebUI: http://127.0.0.1:6099/webui  (默认 token: napcat)"
}

# ============================================================
# 3. Python 虚拟环境 + 依赖
# ============================================================
if [ ! -d ".venv" ]; then
    info "创建虚拟环境..."
    python3 -m venv .venv
fi
source .venv/bin/activate

info "安装 Python 依赖..."
PIP_FLAGS="-q"
python3 -c "import sys; exit(0 if sys.version_info >= (3,14) else 1)" 2>/dev/null && PIP_FLAGS="$PIP_FLAGS --only-binary :all:"
pip install $PIP_FLAGS -r requirements.txt

# ============================================================
# 4. 前端构建
# ============================================================
if [ ! -d "web/node_modules" ]; then
    info "安装前端依赖..."
    (cd web && npm install)
fi
if [ ! -f "server/webui/static/index.html" ] || [ "web/src" -nt "server/webui/static/index.html" ]; then
    info "构建前端..."
    (cd web && npm run build)
fi

# ============================================================
# 5. 加载 .env
# ============================================================
if [ -f "$SCRIPT_DIR/.env" ]; then
    info "加载 .env 配置..."
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ============================================================
# 6. 启动 NapCatQQ
# ============================================================
if command -v docker &>/dev/null; then
    start_napcat_docker
else
    warn "未检测到 Docker，跳过 NapCatQQ 启动"
    warn "请手动启动 NapCatQQ 或安装 Docker: curl -fsSL https://get.docker.com | sh"
fi

# ============================================================
# 7. 启动服务器
# ============================================================
PORT="${2:-8080}"
if lsof -ti:"$PORT" &>/dev/null; then
    info "端口 $PORT 已被占用，清理旧进程..."
    lsof -ti:"$PORT" | xargs kill -9 2>/dev/null
    sleep 0.5
fi
echo ""
info "嘟嘟鲨鱼 启动中... 啊呜～"
echo "  WebUI:  http://127.0.0.1:$PORT"
echo "  NapCat: http://127.0.0.1:6099/webui"
echo ""
export DUDUSHARK_DATA="$SCRIPT_DIR/data"
exec python3 -m server.main "$@"
