#!/bin/bash
# Bulk OneTagger driver.
# Iterates batch files in $BATCH_DIR, runs onetagger-cli per batch via `docker exec`,
# logs per-batch summary + raw OneTagger output to $LOG. Continues on per-batch errors;
# the run is resumable because the profile has skipTagged:true.

set -uo pipefail

BATCH_DIR="${BATCH_DIR:-/tmp/bulk-batches}"
LOG="${LOG:-/tmp/ot-bulk.log}"
CONTAINER="${CONTAINER:-onetagger}"
CONFIG="${CONFIG:-/config/auto-tag.json}"

shopt -s nullglob
batches=("$BATCH_DIR"/b*)
TOTAL="${#batches[@]}"
if [ "$TOTAL" -eq 0 ]; then
  echo "no batch files in $BATCH_DIR" >&2
  exit 1
fi
TOTAL_FILES=$(cat "$BATCH_DIR"/b* | wc -l)

START=$(date +%s)
MODE_TAG=""; [ "${DRY_RUN:-0}" = "1" ] && MODE_TAG="  [DRY RUN — no tagging]"
{
  echo "=== bulk run started $(date -Iseconds)${MODE_TAG} ==="
  echo "    batches=$TOTAL  files=$TOTAL_FILES  container=$CONTAINER  config=$CONFIG"
} > "$LOG"

CUM_OK=0
N=0
for f in "${batches[@]}"; do
  N=$((N+1))
  BSIZE=$(wc -l < "$f")
  BSTART=$(date +%s)
  TMP=$(mktemp)

  # pipe container-rooted paths on stdin, write playlist to tmpfs, run autotagger
  # (DRY_RUN=1 swaps the tagger for an echo — exercises the whole pipeline w/o writing tags)
  if [ "${DRY_RUN:-0}" = "1" ]; then
    INNER='cat > /tmp/b.m3u8; n=$(wc -l < /tmp/b.m3u8); echo "[dry-run] received $n paths; first 3:"; head -3 /tmp/b.m3u8; echo "[dry-run] would run: onetagger-cli autotagger --path /tmp/b.m3u8 --config '"$CONFIG"'"'
  else
    INNER="cat > /tmp/b.m3u8; onetagger-cli autotagger --path /tmp/b.m3u8 --config $CONFIG"
  fi
  sed 's#^#/music/#' "$f" \
    | docker exec -i "$CONTAINER" sh -c "$INNER" \
    > "$TMP" 2>&1
  rc=$?

  bok=$(grep -c "State: Ok" "$TMP" 2>/dev/null || true)
  CUM_OK=$((CUM_OK + bok))
  bsecs=$(( $(date +%s) - BSTART ))
  elapsed=$(( $(date +%s) - START ))

  {
    echo
    printf -- "--- batch %02d/%02d  files=%d  ok=%d  rc=%d  batch=%ds  cum_ok=%d  elapsed=%ds  (%s) ---\n" \
            "$N" "$TOTAL" "$BSIZE" "$bok" "$rc" "$bsecs" "$CUM_OK" "$elapsed" "$(date +%H:%M:%S)"
    cat "$TMP"
  } >> "$LOG"
  rm -f "$TMP"
done

total_elapsed=$(( $(date +%s) - START ))
{
  echo
  echo "=== bulk run complete $(date -Iseconds) ==="
  echo "    total_ok=$CUM_OK / $TOTAL_FILES  elapsed=${total_elapsed}s"
} >> "$LOG"

echo "done: $CUM_OK / $TOTAL_FILES matched, ${total_elapsed}s elapsed, log: $LOG"
