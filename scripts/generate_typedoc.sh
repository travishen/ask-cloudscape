#!/usr/bin/env bash
set -Eeuo pipefail
DEBUG=${DEBUG:-0}
[[ "$DEBUG" == "1" ]] && set -x
trap 'echo "❌ [outer] Failed at line $LINENO" >&2' ERR

# 讀 .env
if [ -f ".env" ]; then
  set -o allexport
  source .env
  set +o allexport
fi

NODE_IMAGE=${NODE_IMAGE:-node:20-bookworm-slim}
TD_OUT=${TD_OUT:-data/typedoc_md}
PACKAGES=${PACKAGES:-"@cloudscape-design/components"}

mkdir -p "$TD_OUT"

# 進容器環境變數（含可寫 HOME / npm 快取，與代理）
DOCKER_ENV=(
  -e DEBUG="${DEBUG}"
  -e HOME="/tmp/home"
  -e NPM_CONFIG_CACHE="/tmp/.npm"
  -e npm_config_cache="/tmp/.npm"
)
if [ -n "${PROXY_SERVER:-}" ]; then
  DOCKER_ENV+=(
    -e HTTP_PROXY="$PROXY_SERVER"
    -e HTTPS_PROXY="$PROXY_SERVER"
    -e npm_config_proxy="$PROXY_SERVER"
    -e npm_config_https_proxy="$PROXY_SERVER"
  )
fi

# 你用 root 也行；若想用目前使用者，改成： --user "$(id -u):$(id -g)"
USER_FLAG=(--user root)

echo "[outer] Using image: $NODE_IMAGE"
echo "[outer] Output dir: $TD_OUT"
echo "[outer] Packages: $PACKAGES"

docker run --rm -t \
  "${DOCKER_ENV[@]}" \
  -v "$PWD/$TD_OUT":/out \
  "${USER_FLAG[@]}" \
  "$NODE_IMAGE" bash -lc '
set -Eeuo pipefail
[[ "${DEBUG:-0}" == "1" ]] && set -x
trap '\''echo "❌ [inner] Failed at line $LINENO" >&2'\'' ERR

mkdir -p "${HOME:-/tmp/home}" /tmp/.npm
echo "[inner] Node: $(node -v) | npm: $(npm -v)"
echo "[inner] PACKAGES: '"$PACKAGES"' | OUT: /out"

# 臨時專案
mkdir -p /tmp/project && cd /tmp/project
npm config set cache "${NPM_CONFIG_CACHE:-/tmp/.npm}" --global || true
npm init -y

# 安裝 typedoc 與所需型別（解析 React/TS）
npm i typedoc typedoc-plugin-markdown typescript @types/react @types/react-dom @types/node '"$PACKAGES"'

# 動態 tsconfig：include 你要的套件（node_modules 下的原始碼/型別）
cat > tsconfig.json << "EOF"
{
  "compilerOptions": {
    "target": "ES2021",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "allowJs": true,
    "checkJs": false,
    "jsx": "react-jsx",
    "lib": ["ES2021", "DOM", "DOM.Iterable"],
    "types": ["node", "react", "react-dom"]
  },
  "include": [
    // __INJECT_INCLUDE__
  ]
}
EOF

# 依 PACKAGES 生成 include 清單
includes=()
for pkg in '"$PACKAGES"'; do
  includes+=("\"node_modules/$pkg/**/*\"")
done
inc_line=$(IFS=, ; echo "${includes[*]}")
# 替換占位符
sed -i "s|// __INJECT_INCLUDE__|$inc_line|" tsconfig.json

echo "[inner] tsconfig.json:"
cat tsconfig.json

# 對每個套件跑 typedoc（expand 策略）
for pkg in '"$PACKAGES"'; do
  safe=$(echo "$pkg" | sed "s/@//; s@/@__@g")
  mkdir -p "/out/$safe"
  npx typedoc \
    --plugin typedoc-plugin-markdown \
    --entryPointStrategy expand \
    --entryPoints "node_modules/$pkg" \
    --tsconfig tsconfig.json \
    --out "/out/$safe"
  echo "[inner] TypeDoc generated for $pkg -> /out/$safe"
done

echo "[inner] Done."
' || { echo "❌ TypeDoc generation failed (see logs above)"; exit 1; }
