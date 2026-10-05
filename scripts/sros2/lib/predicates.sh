#!/usr/bin/env bash
# verify-env.sh と probe.sh が使う判定の述語（docker を使わない。scripts/test-probe.sh が単体テストする）。
# check <id> <説明> <述語> <引数...> に渡す。述語は条件が成り立つときに 0 を返す。

# 同じ値
eq() { [ "$1" = "$2" ]; }

# 正の整数（空・非数値は偽）
gt_zero() { [ "${1:-0}" -gt 0 ] 2>/dev/null; }

# 整数の $1 <= $2
le() { [ "$1" -le "$2" ] 2>/dev/null; }

# 空でなく、2 つが異なる
nonempty_and_differ() { [ -n "$1" ] && [ "$1" != "$2" ]; }

# publisher が起動できたか（ログに PUB_UP が出ている）。 pub_started <publisher のログ>
pub_started() { [ -n "${1:-}" ] && grep -q PUB_UP "$1" 2>/dev/null; }

# 拒否された: 受信が 0 件で、publisher は起動できていた（起動していないための 0 件を除く）。
#   rejected <受信数> <publisher のログ>
rejected() { [ "$1" = "0" ] && pub_started "$2"; }

# 鍵なしの参加者用: 「受信 0 件・見えた publisher 0」で、publisher は起動できていた。
#   rejected_pair "<受信数> <見えた数>" <ログ>
rejected_pair() { [ "$1" = "0 0" ] && pub_started "$2"; }

# governance: 未認証を許可しない・RTPS / discovery が ENCRYPT・保護なし（NONE）がない
gov_ok() {
  grep -q "<allow_unauthenticated_participants>false<" <<<"$1" &&
    grep -q "<rtps_protection_kind>ENCRYPT<" <<<"$1" &&
    grep -q "<discovery_protection_kind>ENCRYPT<" <<<"$1" &&
    ! grep -Eq "_protection_kind>NONE<" <<<"$1"
}

# permissions: ワイルドカードなし・default が DENY
perm_ok() {
  ! grep -Eq "<topic>[^<]*\*" <<<"$1" && grep -q "<default>DENY</default>" <<<"$1"
}

# 暗号化されている: RTPS のパケットがあり（$1）、payload は平文で出ず（$2 = 0）、受信もある（$3 > 0）。
# 「平文が 0 件」は通信が流れていなかっただけでも成り立つため、受信があることも条件にする
encrypted_ok() { [ "$1" -gt 0 ] && [ "$2" -eq 0 ] && [ "${3:-0}" -gt 0 ]; }

# 「cert=N crl=M」の両方が 14 以上
bash_ge14() {
  local c="${1#cert=}" r="${1##*crl=}"
  c="${c%% *}"
  [ "$c" -ge 14 ] 2>/dev/null && [ "$r" -ge 14 ] 2>/dev/null
}
