#!/bin/sh
# SROS2 用の entrypoint。read-only で渡された自分の enclave（/sros2/src）を、
# コンテナ内の tmpfs（/run/sros2/keystore）へ 0700/0600 でコピーしてから、元の entrypoint に渡す。
# macOS の bind mount では所有者・パーミッションを制御できず、秘密鍵が緩いまま見えてしまうため。
# /sros2/src/enclave.env の PLACE_AT は、enclave の置き場（例: /lab/ros2lab_a）。
# SROS2 が有効（ROS_SECURITY_ENABLE=true）なのに鍵が揃っていないときは、起動しない（fail closed）。
# 鍵なしのまま「健全なコンテナ」として立ち上がり、セキュリティなしで動き続けるのを防ぐため。
#   - ROS_SECURITY_ENABLE は大文字小文字を区別せず true を有効とみなす。true / false / 空以外は拒否する
#     （ros2 側が有効と解釈する値を、ここで見逃さないため）。
#   - 鍵ファイルは空でないこと（空ファイルを弾く）。
#   - SROS2_REQUIRE_CRL=true のとき（環境 c）は crl.pem も必須。true / false / 空以外は拒否する。
# 任意の設定（ros2arm のように、ノードが root 以外で動き、デスクトップの端末がコンテナの ENV を継承しないコンテナ向け）:
#   - SROS2_CHOWN=<user[:group]>: 鍵の置き場（DST）の所有者を変える。モード（0700/0600）は変えない。
#     user[:group] の形以外は拒否する。変更に失敗したら起動しない（鍵が読めないまま起動しないため）。
#   - SROS2_ENV_FILE=<path>: SROS2 が有効なときだけ、ROS_SECURITY_ENABLE / STRATEGY / KEYSTORE / ENCLAVE_OVERRIDE を
#     source できる形で書く（0644。鍵は含まない）。値は source されるので、英数字と _ . / - 以外を含むものは拒否する。
# SROS2_SRC / SROS2_DST / SROS2_NEXT はテスト用の上書き。
set -eu
# 鍵を置く間だけ、作るディレクトリ・ファイルを自分だけが読める権限にする（途中のディレクトリも 0700）。
# 次の entrypoint には元の umask で渡す（./workspace などに作るファイルが 0600 にならないように）
old_umask="$(umask)"
umask 077

SRC="${SROS2_SRC:-/sros2/src}"
DST="${SROS2_DST:-/run/sros2/keystore}"
NEXT="${SROS2_NEXT:-/ros_entrypoint.sh}"

die() {
  echo "sros2: $*" >&2
  exit 1
}

enable="$(printf '%s' "${ROS_SECURITY_ENABLE:-}" | tr '[:upper:]' '[:lower:]')"
case "$enable" in
  true)
    [ -f "$SRC/enclave.env" ] || die "$SRC/enclave.env が無い。鍵が渡されていないため起動しない。"
    required="cert.pem key.pem identity_ca.cert.pem permissions_ca.cert.pem governance.p7s permissions.p7s"
    case "$(printf '%s' "${SROS2_REQUIRE_CRL:-}" | tr '[:upper:]' '[:lower:]')" in
      true) required="$required crl.pem" ;;
      "" | false) ;;
      *) die "SROS2_REQUIRE_CRL の値が不正: '${SROS2_REQUIRE_CRL:-}'（true / false か未設定にすること）" ;;
    esac
    for f in $required; do
      [ -s "$SRC/$f" ] || die "$SRC/$f が無い、または空。鍵が揃っていないため起動しない。"
    done
    # enclave.env は source せず、PLACE_AT の値だけを読む（ファイルの中身をシェルとして実行しない）
    PLACE_AT="$(sed -n 's/^PLACE_AT=//p' "$SRC/enclave.env" | head -n 1)"
    # 置き場は /lab/<英数字と _ のみ>。.. や空は拒否する（パスの生成に使うため）
    case "$PLACE_AT" in
      "" | *..* | *[!A-Za-z0-9_/]* | /) die "enclave.env の PLACE_AT が不正: '$PLACE_AT'" ;;
      /*) ;;
      *) die "enclave.env の PLACE_AT は / で始まること: '$PLACE_AT'" ;;
    esac
    dir="$DST/enclaves$PLACE_AT"
    mkdir -p "$dir"
    for f in "$SRC"/*; do
      case "$(basename "$f")" in
        enclave.env) ;;
        *) cp "$f" "$dir/" ;;
      esac
    done
    chmod 0700 "$DST" "$DST/enclaves" "$dir"
    chmod 0600 "$dir"/*
    if [ -n "${SROS2_CHOWN:-}" ]; then
      # user[:group]（英数字と _ . - のみ。先頭が - や . のものは chown のオプション・パスと誤解されうるので拒否）
      case "$SROS2_CHOWN" in
        -* | .* | *[!A-Za-z0-9_.:-]*) die "SROS2_CHOWN の値が不正: '$SROS2_CHOWN'（user[:group] の形にすること）" ;;
      esac
      chown -R "$SROS2_CHOWN" "$DST" || die "SROS2_CHOWN=$SROS2_CHOWN で鍵の所有者を変えられない。鍵が読めないまま起動しないため中止する。"
    fi
    if [ -n "${SROS2_ENV_FILE:-}" ]; then
      for v in ROS_SECURITY_ENABLE ROS_SECURITY_STRATEGY ROS_SECURITY_KEYSTORE ROS_SECURITY_ENCLAVE_OVERRIDE; do
        eval "val=\${$v:-}"
        case "$val" in
          *[!A-Za-z0-9_./-]*) die "$v の値が env ファイルに書けない（英数字と _ . / - のみ）: '$val'" ;;
        esac
      done
      # 先に全部検査してから書く（途中で止まって中途半端なファイルを残さない）。鍵ではないので 0644
      (
        umask 022
        {
          for v in ROS_SECURITY_ENABLE ROS_SECURITY_STRATEGY ROS_SECURITY_KEYSTORE ROS_SECURITY_ENCLAVE_OVERRIDE; do
            eval "val=\${$v:-}"
            if [ -n "$val" ]; then echo "export $v=$val"; fi
          done
        } > "$SROS2_ENV_FILE"
      ) || die "SROS2_ENV_FILE=$SROS2_ENV_FILE を書けない。"
    fi
    ;;
  "" | false) ;;
  *) die "ROS_SECURITY_ENABLE の値が不正: '${ROS_SECURITY_ENABLE:-}'（true / false か未設定にすること）" ;;
esac

umask "$old_umask"
exec "$NEXT" "$@"
