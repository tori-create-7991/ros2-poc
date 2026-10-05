#!/usr/bin/env bash
# verify-env.sh が使う判定の述語（scripts/sros2/lib/predicates.sh）を、docker なしで単体テストする。
# 「0 件受信 = 拒否」が publisher の未起動で通らないこと、governance / permissions / 暗号化の判定が
# 設定の違いを区別できることを確認する。検証スクリプトの回帰を CI で検出するためのテスト。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# shellcheck source=scripts/sros2/lib/predicates.sh
. "$ROOT/scripts/sros2/lib/predicates.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
yes_() { "$@" || fail "真のはずが偽: $*"; }
no_() { if "$@"; then fail "偽のはずが真: $*"; fi; }

# --- 数値・文字列の述語 ---
yes_ eq a a; no_ eq a b
yes_ gt_zero 3; no_ gt_zero 0; no_ gt_zero ""; no_ gt_zero abc
yes_ le 90 91; no_ le 92 91; no_ le abc 91
yes_ nonempty_and_differ x y; no_ nonempty_and_differ "" y; no_ nonempty_and_differ x x
yes_ bash_ge14 "cert=89 crl=179"; no_ bash_ge14 "cert=13 crl=179"; no_ bash_ge14 "cert=89 crl=3"; no_ bash_ge14 ""

# --- publisher の起動と拒否の判定 ---
echo "PUB_UP" > "$TMP/up.log"
: > "$TMP/down.log"
yes_ pub_started "$TMP/up.log"; no_ pub_started "$TMP/down.log"; no_ pub_started "$TMP/none.log"
# 0 件受信でも、publisher が起動していなければ「拒否された」とは言わない（空振りを防ぐ）
yes_ rejected 0 "$TMP/up.log"
no_ rejected 0 "$TMP/down.log"
no_ rejected 0 "$TMP/none.log"
no_ rejected 5 "$TMP/up.log"       # 受信があれば拒否ではない
no_ rejected "" "$TMP/up.log"      # 購読が実行できなかった（空）は拒否ではない
yes_ rejected_pair "0 0" "$TMP/up.log"
no_ rejected_pair "0 0" "$TMP/down.log"
no_ rejected_pair "0 1" "$TMP/up.log"   # publisher が見えているなら、鍵なしでも拒否ではない
no_ rejected_pair "" "$TMP/up.log"

# --- 暗号化の判定（RTPS あり・平文 0・受信あり）---
yes_ encrypted_ok 100 0 20
no_ encrypted_ok 0 0 20      # パケットが取れていない
no_ encrypted_ok 100 5 20    # 平文が出ている
no_ encrypted_ok 100 0 0     # 通信が流れていない（0 件が「暗号化の証拠」にならない）
no_ encrypted_ok 100 0 ""

# --- governance / permissions の判定 ---
GOV_OK='<allow_unauthenticated_participants>false</allow_unauthenticated_participants>
<discovery_protection_kind>ENCRYPT</discovery_protection_kind>
<rtps_protection_kind>ENCRYPT</rtps_protection_kind>
<data_protection_kind>ENCRYPT</data_protection_kind>'
yes_ gov_ok "$GOV_OK"
no_ gov_ok "${GOV_OK//false/true}"                          # 未認証を許可
no_ gov_ok "${GOV_OK//rtps_protection_kind>ENCRYPT/rtps_protection_kind>NONE}"   # RTPS 保護なし
no_ gov_ok "${GOV_OK//rtps_protection_kind>ENCRYPT/rtps_protection_kind>SIGN}"   # 暗号化まで行っていない
no_ gov_ok "$GOV_OK
<data_protection_kind>NONE</data_protection_kind>"          # 保護なしのトピックがある

PERM_OK='<topic>rt/lab/state</topic>
<default>DENY</default>'
yes_ perm_ok "$PERM_OK"
no_ perm_ok "${PERM_OK//DENY/ALLOW}"                        # default が ALLOW
no_ perm_ok "$PERM_OK
<topic>rt/*</topic>"                                         # ワイルドカード
no_ perm_ok "<topic>rt/lab/state</topic>"                   # default が無い

echo "OK: probe predicate tests passed"
