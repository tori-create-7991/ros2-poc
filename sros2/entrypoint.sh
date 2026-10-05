#!/bin/sh
# SROS2 用の entrypoint。read-only で渡された自分の enclave（/sros2/src）を、
# コンテナ内の tmpfs（/run/sros2/keystore）へ 0700/0600 でコピーしてから、元の entrypoint に渡す。
# macOS の bind mount では所有者・パーミッションを制御できず、秘密鍵が緩いまま見えてしまうため。
# /sros2/src/enclave.env の PLACE_AT は、enclave の置き場（例: /lab/ros2lab-a）。
set -eu

SRC=/sros2/src
DST=/run/sros2/keystore

if [ -f "$SRC/enclave.env" ]; then
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

exec /ros_entrypoint.sh "$@"
