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

NAPCAT_DIR="$HOME/NapCatQQ"
NAPCAT_GH="https://github.com/NapNeko/NapCatQQ/releases/latest/download"

# ============================================================
# 1. 环境检查
# ============================================================
if ! command -v python3 &>/dev/null; then
    err "请先安装 Python 3.10+"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYVER"

# ============================================================
# 2. NapCatQQ 安装
# ============================================================
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Darwin)  OS_KEY="macos" ;;
    Linux)   OS_KEY="linux" ;;
    *) err "不支持的操作系统: $OS"; exit 1 ;;
esac

case "$ARCH" in
    x86_64|amd64) ARCH_KEY="x64" ;;
    arm64|aarch64) ARCH_KEY="arm64" ;;
    *) err "不支持的架构: $ARCH"; exit 1 ;;
esac

install_napcat() {
    if [ ! -f "$NAPCAT_DIR/napcat.mjs" ] && [ ! -f "$NAPCAT_DIR/napcat.sh" ]; then
        if ! command -v node &>/dev/null; then
            err "NapCatQQ v4.x 需要 Node.js，请先安装 Node.js 18+"
            return 1
        fi

        info "正在安装 NapCatQQ 到 $NAPCAT_DIR ..."
        local tmpdir
        tmpdir=$(mktemp -d /tmp/napcat_install.XXXXXX)
        trap "rm -rf $tmpdir" RETURN

        for pkg in Framework Shell; do
            local url="${NAPCAT_GH}/NapCat.${pkg}.zip"
            info "下载 NapCat.${pkg}.zip ..."
            if ! curl -fsSL --connect-timeout 15 --retry 2 -o "$tmpdir/NapCat.${pkg}.zip" "$url"; then
                err "下载失败: $url"
                return 1
            fi
        done

        mkdir -p "$NAPCAT_DIR"
        unzip -oq "$tmpdir/NapCat.Framework.zip" -d "$NAPCAT_DIR"
        unzip -oq "$tmpdir/NapCat.Shell.zip" -d "$NAPCAT_DIR"
        info "NapCatQQ 安装完成: $NAPCAT_DIR"
    else
        info "NapCatQQ 已安装: $NAPCAT_DIR"
    fi

    # macOS: 桥接沙盒版 QQ 的版本信息 + 签名原生模块（每次启动都检查）
    if [ "$OS" = "Darwin" ]; then
        local qq_config_dir="$HOME/Library/Application Support/QQ/versions"
        local sandbox_config="$HOME/Library/Containers/com.tencent.qq/Data/Library/Application Support/QQ/versions/config.json"
        if [ -f "$sandbox_config" ] && [ ! -f "$qq_config_dir/config.json" ]; then
            mkdir -p "$qq_config_dir"
            ln -sf "$sandbox_config" "$qq_config_dir/config.json"
            info "已桥接 QQ 版本信息"
        fi

        local qq_pkg="$NAPCAT_DIR/qq_package.json"
        if [ ! -f "$qq_pkg" ]; then
            cat > "$qq_pkg" << 'EOFPKG'
{"version": "6.9.93-47354", "buildVersion": "47354", "name": "qq"}
EOFPKG
        fi

        # 签名 NapCatQQ 原生模块（解决 macOS Gatekeeper 阻止加载未签名 dylib）
        find "$NAPCAT_DIR/native" -name "*.node" -type f 2>/dev/null | while read -r f; do
            codesign --remove-signature "$f" 2>/dev/null || true
            codesign --sign - "$f" 2>/dev/null || true
        done
    fi
}

check_qq() {
    # 检查 QQ 客户端是否可用
    if [ "$OS" = "Darwin" ]; then
        if [ -d "/Applications/QQ.app" ] || [ -d "$HOME/Applications/QQ.app" ]; then
            return 0
        fi
        warn "未检测到 QQ.app，请先安装 Mac QQ"
        warn "下载: https://im.qq.com/macqq/"
        return 1
    fi

    if [ "$OS" = "Linux" ]; then
        if command -v qq &>/dev/null || [ -d "/opt/QQ" ]; then
            return 0
        fi
        warn "未检测到 Linux QQ，正在尝试安装..."
        install_linux_qq
    fi
}

install_linux_qq() {
    local QQ_DEB="https://dldir1.qq.com/qqfile/qq/QQNT/94704804/linuxqq_3.2.23-44343_amd64.deb"
    local QQ_RPM="https://dldir1.qq.com/qqfile/qq/QQNT/94704804/linuxqq_3.2.23-44343_x86_64.rpm"
    local QQ_DEB_ARM="https://dldir1.qq.com/qqfile/qq/QQNT/94704804/linuxqq_3.2.23-44343_arm64.deb"
    local QQ_RPM_ARM="https://dldir1.qq.com/qqfile/qq/QQNT/94704804/linuxqq_3.2.23-44343_aarch64.rpm"

    local url
    if [ "$ARCH_KEY" = "arm64" ]; then
        if command -v dpkg &>/dev/null; then url="$QQ_DEB_ARM"; else url="$QQ_RPM_ARM"; fi
    else
        if command -v dpkg &>/dev/null; then url="$QQ_DEB"; else url="$QQ_RPM"; fi
    fi

    local tmp_deb="/tmp/qq_install.deb"
    info "下载 QQ ($url) ..."
    if ! curl -fsSL --connect-timeout 30 --retry 2 -o "$tmp_deb" "$url"; then
        warn "QQ 下载失败，请手动安装 QQ 客户端后重试"
        return 1
    fi

    info "安装 QQ 客户端（需要 sudo）..."
    if command -v dpkg &>/dev/null; then
        sudo dpkg -i "$tmp_deb"
    else
        sudo rpm -i "$tmp_deb"
    fi
    rm -f "$tmp_deb"
    # 修正可能的权限问题
    sudo chown -R "$(whoami):$(whoami)" "$HOME/.config/QQ" 2>/dev/null || true
    info "QQ 安装完成"
}

# ---- 安装 NapCatQQ ----
install_napcat

# ---- 检查 QQ 客户端 ----
check_qq || warn "QQ 客户端未就绪，NapCatQQ 启动可能失败"

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
# 杀掉占用端口的旧进程
if lsof -ti:"$PORT" &>/dev/null; then
    info "端口 $PORT 已被占用，清理旧进程..."
    lsof -ti:"$PORT" | xargs kill -9 2>/dev/null
    sleep 0.5
fi
echo ""
info "嘟嘟鲨鱼 启动中... 啊呜～"
echo "  WebUI:  http://127.0.0.1:$PORT"
echo "  NapCat: $NAPCAT_DIR"
echo ""
export DUDUSHARK_DATA="$SCRIPT_DIR/data"
python3 -m server.main "$@"
