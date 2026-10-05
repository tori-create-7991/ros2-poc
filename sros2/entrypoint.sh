#!/bin/sh
# SROS2 用の entrypoint。read-only で渡された自分の enclave（/sros2/src）を、
# コンテナ内の tmpfs（/run/sros2/keystore）へ 0700/0600 でコピーしてから、元の entrypoint に渡す。
# macOS の bind mount では所有者・パーミッションを制御できず、秘密鍵が緩いまま見えてしまうため。
# /sros2/src/enclave.env の PLACE_AT は、enclave の置き場（例: /lab/ros2lab_a）。
# SROS2 が有効（ROS_SECURITY_ENABLE=true）なのに鍵が揃っていないときは、起動しない（fail closed）。
# 鍵なしのまま「健全なコンテナ」として立ち上がり、セキュリティなしで動き続けるのを防ぐため。
# SROS2_SRC / SROS2_DST / SROS2_NEXT はテスト用の上書き。
set -eu

SRC="${SROS2_SRC:-/sros2/src}"
DST="${SROS2_DST:-/run/sros2/keystore}"
NEXT="${SROS2_NEXT:-/ros_entrypoint.sh}"

if [ "${ROS_SECURITY_ENABLE:-}" = "true" ]; then
  if [ ! -f "$SRC/enclave.env" ]; then
    echo "sros2: $SRC/enclave.env が無い。鍵が渡されていないため起動しない。" >&2
    exit 1
  fi
  for f in cert.pem key.pem identity_ca.cert.pem permissions_ca.cert.pem governance.p7s permissions.p7s; do
    if [ ! -f "$SRC/$f" ]; then
      echo "sros2: $SRC/$f が無い。鍵が揃っていないため起動しない。" >&2
      exit 1
    fi
  done
  # shellcheck disable=SC1091
  . "$SRC/enclave.env"
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
fi

exec "$NEXT" "$@"
