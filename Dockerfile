FROM ros:jazzy-ros-base

SHELL ["/bin/bash", "-c"]

# sros2: 将来の SROS2 (DDS-Security) 実習用にあらかじめ同梱しておく
RUN apt-get update \
    && apt-get install -y --no-install-recommends ros-jazzy-sros2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 隔離設定: シェルの種類(login/interactive)に依存させず、コンテナ全体に
# 確実に効かせるため ENV で直接設定する
ENV ROS_DOMAIN_ID=42
ENV ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST

# setup.bash の自動 source。/etc/profile.d/ に置くことで、
# `docker compose exec ... bash -lc '...'`（login shell、.bashrc は読まれない）
# でも `docker compose exec ... bash`（対話シェル）でも確実に読まれる
RUN printf '%s\n' \
    '# --- ros2-poc: isolated lab env ---' \
    'source /opt/ros/jazzy/setup.bash' \
    'echo "[ros2lab] DOMAIN=$ROS_DOMAIN_ID discovery=$ROS_AUTOMATIC_DISCOVERY_RANGE"' \
    > /etc/profile.d/ros2lab.sh \
    && echo '[ -f /etc/profile.d/ros2lab.sh ] && source /etc/profile.d/ros2lab.sh' >> /root/.bashrc

WORKDIR /workspace

CMD ["sleep", "infinity"]
