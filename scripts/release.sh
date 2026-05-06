#!/usr/bin/env bash
#
# 一键发版本脚本：本地打 git tag → push 到 GitHub → 触发 GHCR Actions 自动构建推送
#
# 用法：
#   ./scripts/release.sh v1.0.0                  # 标准 release
#   ./scripts/release.sh v1.0.0-rc.1             # 预发布
#   ./scripts/release.sh v1.0.0 --message "..."  # 自定义 tag message
#
# 注意：
#   1. 必须在 main 分支且工作区干净
#   2. tag 格式必须 v<MAJOR>.<MINOR>.<PATCH>[-pre]，匹配 .github/workflows/build-and-push.yml 的 v*.*.*
#   3. 推送 tag 后 GitHub Actions 自动跑（约 25-40 分钟首次 / 2-5 分钟增量）
#   4. 完成后服务器：
#        export GHCR_OWNER=<your-github-username>
#        export IMAGE_TAG=v1.0.0
#        docker compose -f docker-compose.prod.yml --env-file .env pull
#        docker compose -f docker-compose.prod.yml --env-file .env up -d

set -euo pipefail

# ── 颜色 ─────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

err() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }
ok()  { echo -e "${GREEN}✓${NC} $*"; }
info(){ echo -e "${BLUE}→${NC} $*"; }
warn(){ echo -e "${YELLOW}!${NC} $*"; }

# ── 参数解析 ─────────────────────────────────────────────────────────
VERSION="${1:-}"
TAG_MESSAGE=""
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --message|-m) TAG_MESSAGE="$2"; shift 2 ;;
    *) err "未知参数：$1" ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  cat <<'USAGE'
用法：
  ./scripts/release.sh <version> [--message "release notes"]

示例：
  ./scripts/release.sh v1.0.0
  ./scripts/release.sh v1.2.0-rc.1 --message "RC: support skill module"
USAGE
  exit 1
fi

# ── tag 格式校验 ─────────────────────────────────────────────────────
if ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$ ]]; then
  err "tag 格式错误：必须是 v<MAJOR>.<MINOR>.<PATCH>[-pre]，例如 v1.0.0 或 v1.0.0-rc.1"
fi

[[ -z "$TAG_MESSAGE" ]] && TAG_MESSAGE="Release $VERSION"

# ── 仓库状态校验 ─────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[[ -d .git ]] || err "当前目录不是 git 仓库：$ROOT"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  err "必须在 main 分支发版本（当前：$CURRENT_BRANCH）"
fi

if ! git diff-index --quiet HEAD --; then
  err "工作区不干净，请先 commit / stash 所有改动"
fi

if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  warn "有未追踪的文件（不影响发版本，但建议清理）"
fi

# 远端必须能 push
if ! git remote get-url origin > /dev/null 2>&1; then
  err "未配置 origin 远端，先 git remote add origin <github-url>"
fi

# tag 是否已存在
if git rev-parse "$VERSION" > /dev/null 2>&1; then
  err "tag $VERSION 已存在；用其它版本号或先 git tag -d $VERSION && git push origin :refs/tags/$VERSION"
fi

# ── 同步远端 main ────────────────────────────────────────────────────
info "同步远端 main..."
git fetch origin main
LOCAL_SHA=$(git rev-parse main)
REMOTE_SHA=$(git rev-parse origin/main)
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  err "本地 main 与 origin/main 不一致；先 git pull / push 同步"
fi

# ── 二次确认 ─────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  即将发布版本：${GREEN}$VERSION${NC}"
echo "  Tag message ：$TAG_MESSAGE"
echo "  HEAD commit ：$(git log -1 --format='%h %s' main)"
echo "  推送目标    ：origin/$VERSION"
echo "════════════════════════════════════════════════════════════"
echo ""
read -rp "确认发布？(y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  warn "取消"
  exit 0
fi

# ── 打 tag + push ────────────────────────────────────────────────────
info "打 tag $VERSION ..."
git tag -a "$VERSION" -m "$TAG_MESSAGE"

info "推送 tag 到 origin..."
git push origin "$VERSION"

ok "tag $VERSION 已推送"
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  下一步："
echo ""
echo "  1) GitHub Actions 自动开始构建（约 25-40 分钟首次 / 2-5 分钟增量）"
echo "     查看进度：https://github.com/$(git config --get remote.origin.url \
       | sed -E 's|.*github\.com[:/](.+)\.git$|\1|; s|.*github\.com[:/](.+)$|\1|')/actions"
echo ""
echo "  2) 构建完成后服务器更新："
echo "     export IMAGE_TAG=$VERSION"
echo "     docker compose -f docker-compose.prod.yml --env-file .env pull"
echo "     docker compose -f docker-compose.prod.yml --env-file .env up -d"
echo ""
echo "  3) 镜像地址（GHCR）："
OWNER=$(git config --get remote.origin.url \
  | sed -E 's|.*github\.com[:/]([^/]+)/.+|\1|' \
  | tr '[:upper:]' '[:lower:]')
echo "     ghcr.io/$OWNER/aitestplatform-backend:$VERSION"
echo "     ghcr.io/$OWNER/aitestplatform-frontend:$VERSION"
echo "════════════════════════════════════════════════════════════"
