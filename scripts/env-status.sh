#!/usr/bin/env bash
# 起動中のコンテナが、どの SROS2 環境（ラベル ros2poc.env）と設定で動いているかを表示する。
#   bash scripts/env-status.sh
set -euo pipefail

names="$(docker ps --filter 'label=ros2poc.env' --format '{{.Names}}')"
if [ -z "$names" ]; then
  echo "SROS2 環境のラベルが付いたコンテナは起動していない。"
  exit 0
fi

printf '%-14s %-6s %-9s %s\n' CONTAINER ENV STRATEGY ENCLAVE
for name in $names; do
  label="$(docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' "$name")"
  envs="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$name")"
  strategy="$(sed -n 's/^ROS_SECURITY_STRATEGY=//p' <<<"$envs")"
  enclave="$(sed -n 's/^ROS_SECURITY_ENCLAVE_OVERRIDE=//p' <<<"$envs")"
  printf '%-14s %-6s %-9s %s\n' "$name" "$label" "${strategy:--}" "${enclave:--}"
done
