#!/usr/bin/env bash
# keystore の入れ替え・復元・ロック（scripts/sros2/lib/swap.sh）を、docker・ros2・openssl なしで単体テストする。
# 入れ替えが ks / ca / rogue のどこで失敗しても、元の内容に戻り、OLD が残らないこと。
# 復元自体が失敗したときに、残りを試して旧データを OLD に残すこと。ロックが排他になること。
# 入れ替えの最中に中断されたとき（trap から呼ぶ cleanup_swap）に元に戻ること、を確認する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# shellcheck source=scripts/sros2/lib/swap.sh
. "$ROOT/scripts/sros2/lib/swap.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 作業場所を作る。 setup <初回か: first|existing>
setup() {
  rm -rf "$TMP/base"
  BASE="$TMP/base"
  mkdir -p "$BASE"
  OLD="$BASE/.old-t"
  FINAL_KS="$BASE/keystores/t"
  FINAL_CA="$BASE/ca-private/t"
  FINAL_ROGUE="$BASE/rogue/t"
  KS_OUT="$BASE/.stage-t/keystores"
  CA_OUT="$BASE/.stage-t/ca-private"
  ROGUE_OUT="$BASE/.stage-t/rogue"
  mkdir -p "$KS_OUT" "$CA_OUT" "$ROGUE_OUT"
  echo new > "$KS_OUT/v"; echo new > "$CA_OUT/v"; echo new > "$ROGUE_OUT/v"
  if [ "$1" = existing ]; then
    mkdir -p "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"
    echo old > "$FINAL_KS/v"; echo old > "$FINAL_CA/v"; echo old > "$FINAL_ROGUE/v"
  fi
  MOVED=""; DONE=""; SWAPPING=0
  unset SROS2_ALLOW_TEST_HOOKS SROS2_TEST_FAIL_SWAP SROS2_TEST_FAIL_ROLLBACK
}
val() { cat "$1/v" 2>/dev/null || echo "(無い)"; }
quiet() { "$@" 2>"$TMP/err"; }

# 1) 成功: 3 つとも新しくなり、OLD は残らない
setup existing
quiet swap_all || fail "成功するはずが失敗"
for d in "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"; do [ "$(val "$d")" = new ] || fail "成功: $d が新しくなっていない"; done
[ ! -e "$OLD" ] || fail "成功: OLD が残っている"
[ "$SWAPPING" = 0 ] || fail "成功: SWAPPING が戻っていない"

# 2) 初回（旧がない）の成功
setup first
quiet swap_all || fail "初回: 成功するはずが失敗"
[ "$(val "$FINAL_KS")" = new ] || fail "初回: 新しくなっていない"
[ ! -e "$OLD" ] || fail "初回: OLD が残っている"

# 3) どこで失敗しても、旧に戻り OLD は残らない
for name in ks ca rogue; do
  setup existing
  SROS2_ALLOW_TEST_HOOKS=1 SROS2_TEST_FAIL_SWAP=$name quiet swap_all && fail "$name: 失敗するはずが成功"
  for d in "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"; do [ "$(val "$d")" = old ] || fail "$name で失敗: $d が元に戻っていない（$(val "$d")）"; done
  [ ! -e "$OLD" ] || fail "$name で失敗: OLD が残っている"
  grep -q "元に戻す" "$TMP/err" || fail "$name で失敗: 復元のメッセージが無い"
done

# 4) 初回（旧がない）で失敗したら、入れた新しいものを消す（旧が無いので何も戻さない）
setup first
SROS2_ALLOW_TEST_HOOKS=1 SROS2_TEST_FAIL_SWAP=rogue quiet swap_all && fail "初回の失敗: 失敗するはずが成功"
for d in "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"; do [ ! -e "$d" ] || fail "初回の失敗: $d が残っている"; done

# 5) フックは SROS2_ALLOW_TEST_HOOKS=1 のときだけ効く（環境に残っていても本番の生成を壊さない）
setup existing
SROS2_TEST_FAIL_SWAP=ks quiet swap_all || fail "ALLOW なしでフックが効いた"

# 6) 復元自体が失敗したら、残りを全部試し、失敗した旧データの場所を表示して OLD を残す
setup existing
SROS2_ALLOW_TEST_HOOKS=1 SROS2_TEST_FAIL_SWAP=rogue SROS2_TEST_FAIL_ROLLBACK=ks quiet swap_all && fail "復元失敗: 失敗するはずが成功"
[ "$(val "$FINAL_CA")" = old ] || fail "復元失敗: ca が戻っていない（残りを試していない）"
[ "$(val "$OLD/ks")" = old ] || fail "復元失敗: 戻せなかった旧 ks が OLD に無い"
grep -q "$OLD/ks" "$TMP/err" || fail "復元失敗: 旧データの場所が表示されない: $(cat "$TMP/err")"
# Docker 経由（コンテナ内のパスが BASE）でも、メッセージにはホスト側のパスを出す
setup existing
SROS2_DISPLAY_BASE="/host/sros2" SROS2_ALLOW_TEST_HOOKS=1 SROS2_TEST_FAIL_SWAP=rogue SROS2_TEST_FAIL_ROLLBACK=ks quiet swap_all || true
grep -q "/host/sros2/.old-t/ks" "$TMP/err" || fail "ホスト側のパスが表示されない: $(cat "$TMP/err")"
if grep -q "$BASE" "$TMP/err"; then fail "コンテナ内のパスが表示されている: $(cat "$TMP/err")"; fi

# 7) 入れ替えの最中に中断された（trap から cleanup_swap が呼ばれる）ら、元に戻す
setup existing
mkdir -p "$OLD"
SWAPPING=1
swap_one ks && swap_one ca    # rogue の前で中断された状態
[ "$(val "$FINAL_KS")" = new ] || fail "中断の前提: ks が新しくなっていない"
quiet cleanup_swap
for d in "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"; do [ "$(val "$d")" = old ] || fail "中断: $d が元に戻っていない（$(val "$d")）"; done
[ ! -e "$OLD" ] || fail "中断: OLD が残っている"
[ "$SWAPPING" = 0 ] || fail "中断: SWAPPING が戻っていない"
# 入れ替えの最中でなければ cleanup_swap は何もしない
setup existing
quiet cleanup_swap
for d in "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"; do [ "$(val "$d")" = old ] || fail "通常終了: $d が変わった"; done

# 8) ロック: 2 つ目は取れず、1 つ目のロックを消さない
setup first
acquire_lock "$BASE/.lock-t" || fail "ロックが取れない"
if acquire_lock "$BASE/.lock-t"; then fail "ロックが 2 重に取れた"; fi
[ -d "$BASE/.lock-t" ] || fail "2 つ目の失敗が 1 つ目のロックを消した"

# 9) OLD がある（前回が途中で終わった）なら、旧データに触らない判定になる
setup existing
mkdir -p "$OLD/ks"; echo keep > "$OLD/ks/v"
old_is_clear "$OLD" && fail "OLD があるのに clear と判定した"
[ "$(val "$OLD/ks")" = keep ] || fail "判定が OLD を変えた"
rm -rf "$OLD"
old_is_clear "$OLD" || fail "OLD が無いのに clear でないと判定した"

# 10) 入れ替えが確定したあと、旧の削除中に中断されても、成功済みの入れ替えを巻き戻さない
setup existing
# shellcheck disable=SC2317,SC2329  # swap_all の中から呼ばれる（remove_old を差し替える）
remove_old() {   # OLD の削除の直前に中断された状態を作る（trap から cleanup_swap が呼ばれる）
  cleanup_swap 2>/dev/null
  rm -rf "$OLD"
}
quiet swap_all || fail "確定後の中断: 成功するはずが失敗"
remove_old() { rm -rf "$OLD"; }   # 元に戻す
for d in "$FINAL_KS" "$FINAL_CA" "$FINAL_ROGUE"; do [ "$(val "$d")" = new ] || fail "確定後の中断: $d が巻き戻された（$(val "$d")）"; done

# 11) 記録だけあって退避できていない名前（mv の直前・直後の中断）は、復元で飛ばす。戻し先に入れ子で移さない
setup existing
mkdir -p "$OLD"
MOVED=" ks"       # 記録はあるが OLD/ks は無い（退避前に中断された）
DONE=""
quiet rollback || fail "未退避の記録: 復元が失敗した"
[ "$(val "$FINAL_KS")" = old ] || fail "未退避の記録: 元の ks が変わった"
[ ! -e "$FINAL_KS/ks" ] || fail "未退避の記録: 入れ子ができた"
# 戻し先が残っているのに OLD に旧がある（新が入ったまま中断された）なら、入れ子にせず失敗として OLD を残す
setup existing
mkdir -p "$OLD/ks"; echo older > "$OLD/ks/v"
MOVED=" ks"; DONE=""
quiet rollback && fail "戻し先が残っている: 失敗のはずが成功"
[ ! -e "$FINAL_KS/ks" ] || fail "戻し先が残っている: 入れ子で移した"
[ "$(val "$OLD/ks")" = older ] || fail "戻し先が残っている: OLD の旧データが消えた"

echo "OK: swap tests passed"
