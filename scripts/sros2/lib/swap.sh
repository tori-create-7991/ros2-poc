#!/usr/bin/env bash
# gen-keystore-inner.sh のロック・入れ替え・復元（source して使う。scripts/test-swap.sh が単体テストする）。
#
# 入れ替えは「旧を OLD へ退避 → 新を入れる」を ks / ca / rogue の順に行う。途中で失敗しても、中断（INT / TERM / HUP）
# されても、入れた新しいものを消して退避した旧を元の場所へ戻す（keystore と ca-private が食い違わないように）。
# SIGKILL だけは救えない。そのときは OLD が残るので、次の生成は旧データを消さずに中止する。
#
# 呼び出し側が設定する変数: OLD（退避先）/ FINAL_KS FINAL_CA FINAL_ROGUE（最終の場所）/ KS_OUT CA_OUT ROGUE_OUT（新しいもの）
# テスト用のフック（SROS2_ALLOW_TEST_HOOKS=1 のときだけ効く。通常は使わない）:
#   SROS2_TEST_FAIL_SWAP=<ks|ca|rogue>   その名前の入れ替えを失敗させる
#   SROS2_TEST_FAIL_ROLLBACK=<ks|ca|rogue>   その名前の復元を失敗させる

final_of() { case "$1" in ks) echo "$FINAL_KS" ;; ca) echo "$FINAL_CA" ;; rogue) echo "$FINAL_ROGUE" ;; esac; }
new_of() { case "$1" in ks) echo "$KS_OUT" ;; ca) echo "$CA_OUT" ;; rogue) echo "$ROGUE_OUT" ;; esac; }

test_hook() { [ "${SROS2_ALLOW_TEST_HOOKS:-}" = "1" ] && [ "${!1:-}" = "$2" ]; }

# メッセージに出すパス。Docker 経由ではコンテナ内のパス（/w/...）になるので、ホスト側のパス
# （SROS2_DISPLAY_BASE。gen-keystore.sh が渡す）に読み替える。 shown <パス>
shown() {
  local p="$1" base="${BASE:-}" disp="${SROS2_DISPLAY_BASE:-${BASE:-}}"
  if [ -n "$base" ] && [ "${p#"$base"}" != "$p" ]; then printf '%s' "$disp${p#"$base"}"; else printf '%s' "$p"; fi
}

MOVED=""        # 旧を OLD へ退避した名前
DONE=""         # 新しいものを入れた名前
SWAPPING=0      # 入れ替えの最中（このときに中断されたら trap から rollback する）

# 同じ環境の生成が同時に走ると、STAGE や OLD を壊し合うので排他する（mkdir は原子的）。 acquire_lock <ロックのパス>
acquire_lock() { mkdir "$1" 2>/dev/null; }

# 前回の入れ替えが途中で失敗して、旧データが OLD に残っていないか。 old_is_clear <OLD のパス>
old_is_clear() { [ ! -e "$1" ]; }

swap_one() {
  local name="$1" f n
  test_hook SROS2_TEST_FAIL_SWAP "$name" && return 1
  f="$(final_of "$name")"
  n="$(new_of "$name")"
  mkdir -p "$(dirname "$f")" || return 1
  if [ -e "$f" ]; then
    # 記録を先にする（mv の直後に中断されても、rollback が退避した旧を見つけられるように。
    # 実際に退避できていない名前は、rollback が OLD に無いことを見て飛ばす）
    MOVED="$MOVED $name"
    mv "$f" "$OLD/$name" || return 1
  fi
  DONE="$DONE $name"
  mv "$n" "$f" || return 1
}

# 復元。ベストエフォート: 1 つ失敗しても残りを試し、失敗した名前と旧データの場所を表示する。全部できたら 0
rollback() {
  local name f failed=0
  for name in $DONE; do
    f="$(final_of "$name")"
    rm -rf "$f" || { echo "復元に失敗: 新しい $(shown "$f") を消せない" >&2; failed=1; }
  done
  for name in $MOVED; do
    f="$(final_of "$name")"
    # 退避できていない（OLD に無い）名前は、戻すものが無いので飛ばす
    [ -e "$OLD/$name" ] || continue
    # 戻し先が残っていると、mv は中へ入れ子で移してしまう。残っていたら戻さずに失敗として残す
    if test_hook SROS2_TEST_FAIL_ROLLBACK "$name" || [ -e "$f" ] || ! mv "$OLD/$name" "$f"; then
      echo "復元に失敗: 旧データは $(shown "$OLD/$name") にある（$(shown "$f") へ手で戻すか、再生成する）" >&2
      failed=1
    fi
  done
  DONE=""
  MOVED=""
  [ "$failed" -eq 0 ] && rmdir "$OLD" 2>/dev/null
  return "$failed"
}

# 退避した旧データの削除（テストが差し替えて、削除の最中の中断を再現する）
remove_old() { rm -rf "$OLD"; }

# 入れ替え。成功したら 0。失敗したら元に戻して 1（戻せなかったら OLD を残す）
swap_all() {
  SWAPPING=1
  if ! { mkdir -p "$OLD" && swap_one ks && swap_one ca && swap_one rogue; }; then
    echo "keystore の入れ替えに失敗した。元に戻す。" >&2
    rollback || true
    SWAPPING=0
    return 1
  fi
  # 入れ替えはここで確定した。以降に中断されても巻き戻さない（成功済みの入れ替えを壊さない）
  SWAPPING=0
  MOVED=""
  DONE=""
  # 旧は不要。消せなくても入れ替えは済んでいる（OLD が残ると、次の生成が旧データを消さずに中止する）
  remove_old || echo "警告: $(shown "$OLD") を削除できなかった。手で削除すること。" >&2
}

# 終了時の後始末（trap から呼ぶ）。入れ替えの最中に中断されたら、先に元に戻す
cleanup_swap() {
  if [ "$SWAPPING" = "1" ]; then
    echo "入れ替えの最中に中断された。元に戻す。" >&2
    rollback || true
    SWAPPING=0
  fi
}
