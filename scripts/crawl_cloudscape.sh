#!/usr/bin/env bash
set -euo pipefail

if [ -f ".env" ]; then
  set -o allexport
  source .env
  set +o allexport
fi

OUT_DIR=${OUT_DIR:-data/wacz}
COLLECTION=${COLLECTION:-cloudscape}
SEEDS=${SEEDS:-"https://cloudscape.design/get-started/ https://cloudscape.design/components/ https://cloudscape.design/patterns/"}
WORKERS=${WORKERS:-3}
DISK_UTIL=${DISK_UTIL:-0}
MORE_ARGS=${MORE_ARGS:-}

mkdir -p "$OUT_DIR"

URL_FLAGS=""
for u in $SEEDS; do
  URL_FLAGS="$URL_FLAGS --url $u"
done

CMD=(docker run --rm -it
  -v "$PWD/$OUT_DIR":/crawls
)

CMD+=(webrecorder/browsertrix-crawler crawl
  ${URL_FLAGS}
  --scopeType domain
  --workers "${WORKERS}"
  --diskUtilization "${DISK_UTIL}"
  --text
  --generateWACZ
  --collection "${COLLECTION}"
  ${MORE_ARGS}
)

echo "Running: ${CMD[@]}"
"${CMD[@]}"
echo "Done. Check ${OUT_DIR}/collections/${COLLECTION}/*.wacz"
