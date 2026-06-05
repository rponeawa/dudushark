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
if ! command -v python3 &>/dev/null; then
    err "请先安装 Python 3.10+"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYVER"

# Docker（NapCatQQ 运行环境）
if ! command -v docker &>/dev/null; then
    err "请先安装 Docker: https://docs.docker.com/get-docker/"
    exit 1
fi
info "Docker 已就绪"

# ============================================================
# 2. Python 虚拟环境 + 依赖
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
# 3. 前端构建
# ============================================================
if command -v node &>/dev/null; then
    if [ ! -d "web/node_modules" ]; then
        info "安装前端依赖..."
        (cd web && npm install)
    fi
    if [ ! -f "server/webui/static/index.html" ]; then
        info "构建前端..."
        (cd web && npm run build)
    fi
else
    warn "未检测到 Node.js，跳过前端构建"
fi

# ============================================================
# 4. 拉取 NapCatQQ Docker 镜像
# ============================================================
info "拉取 NapCatQQ Docker 镜像..."
docker pull napneko/napcat 2>/dev/null || warn "镜像拉取失败，将使用本地缓存"

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
# 6. 启动
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
echo ""
export DUDUSHARK_DATA="$SCRIPT_DIR/data"
python3 -m server.main "$@"
