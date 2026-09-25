# ros2-poc

ROS 2 (Jazzy Jalisco) の Pub/Sub・Discovery を、実機や既存の ROS グラフに
一切触れずローカルで安全に試すための隔離実習環境。Colima 上の Docker
コンテナとして立ち上げる。

## 前提

- [Colima](https://github.com/abiosoft/colima)（`colima start`）
- Docker CLI / Docker Compose v2（`docker compose`）

## 起動

```bash
colima start   # 未起動の場合
docker compose up -d --build
```

コンテナに入ると `ROS_DOMAIN_ID=42` / `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`
が自動的に有効になる（`ros2lab` エイリアスとして `.bashrc` に設定済み）。
これにより、同じホスト上の他の ROS グラフや、ネットワーク越しの実機と混線しない。

コンテナ自体もホストとは別ネットワーク名前空間（デフォルト bridge）なので、
二重に隔離されている。

## 使い方

複数ターミナルでの Pub/Sub 実習は、同じコンテナに対して
`docker compose exec` を必要な数だけ実行して行う。

ターミナルA（Subscriber）:

```bash
docker compose exec ros2lab bash
ros2 topic echo /chatter std_msgs/msg/String
```

ターミナルB（Publisher）:

```bash
docker compose exec ros2lab bash
ros2 topic pub /chatter std_msgs/msg/String "{data: 'hello'}" -r 1
```

Discovery の状態を覗く:

```bash
docker compose exec ros2lab bash
ros2 topic list -t
ros2 topic info /chatter -v
```

環境の自己診断:

```bash
docker compose exec ros2lab bash -lc 'ros2 doctor --report | sed -n "1,40p"'
```

## SROS2 について

`ros-jazzy-sros2`（DDS-Security による認証・アクセス制御・暗号化のツール群）
を同梱済み。今回の Pub/Sub 実習では使用しないが、将来 SROS2 を使った隔離
実習を行う際にイメージの再ビルドが不要になるようにしている。

```bash
docker compose exec ros2lab bash -lc 'ros2 pkg list | grep sros2'
```

## 片付け

```bash
docker compose down
```

`./workspace` はコンテナの `/workspace` にマウントされる。SROS2 の
keystore など、永続化したいファイルはここに置く。
