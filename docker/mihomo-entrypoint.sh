#!/bin/sh
# mihomo（Clash Meta）入口：先按 .env 里的订阅地址渲染配置，再交棒给 mihomo。
#
#   CLASH_SUBSCRIBE_URL 有值 → 渲染 config.example.yaml（订阅节点 + 分流规则）
#   CLASH_SUBSCRIBE_URL 为空 → 渲染 config.direct.yaml（全部直连，保证 forager 仍能出网）
#
# 模板与渲染产物都在挂载进来的配置目录里：模板由仓库提供，产物每次启动重写。
# 订阅地址只经环境变量传入，不拼进任何 shell 命令。
set -e

CONF_DIR="${CLASH_HOME:-/root/.config/mihomo}"
URL="${CLASH_SUBSCRIBE_URL:-}"
TARGET="$CONF_DIR/config.yaml"

mkdir -p "$CONF_DIR"

if [ -n "$URL" ]; then
    TAB="$(printf '\t')"
    BAD=""
    case "$URL" in
        http://*|https://*) ;;
        *) BAD="必须以 http:// 或 https:// 开头" ;;
    esac
    case "$URL" in
        *'"'*|*\\*|*" "*|*"$TAB"*) BAD="不能包含双引号、反斜杠或空白字符" ;;
    esac
    if [ -n "$BAD" ]; then
        echo "CLASH_SUBSCRIBE_URL $BAD" >&2
        exit 1
    fi
    TEMPLATE="$CONF_DIR/config.example.yaml"
else
    TEMPLATE="$CONF_DIR/config.direct.yaml"
    echo "CLASH_SUBSCRIBE_URL 未设置：mihomo 以全部直连运行" >&2
fi

# 用 ENVIRON 取值而不是 awk -v（后者会解释 URL 里的反斜杠转义）；
# 占位符按整体子串替换、不走正则，所以 & | $ 等字符原样保留。
awk 'BEGIN { url = ENVIRON["CLASH_SUBSCRIBE_URL"] }
     {
       line = $0; out = ""
       while ((i = index(line, "__SUBSCRIBE_URL__")) > 0) {
         out = out substr(line, 1, i - 1) url
         line = substr(line, i + 17)
       }
       print out line
     }' "$TEMPLATE" > "$TARGET.tmp"
chmod 600 "$TARGET.tmp"   # 产物里有订阅地址
mv -f "$TARGET.tmp" "$TARGET"

exec "${MIHOMO_BIN:-/mihomo}" -d "$CONF_DIR"
