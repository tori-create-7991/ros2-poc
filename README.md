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

## 他のラボ用コンテナとの接続（ros2-lab-net）

`ros2lab` は `ros2-lab-net`（internal: インターネットへの出口なし）にも参加している。
別の compose プロジェクトのコンテナ（例: `kali-vnc`）は、必要なときだけ後付けで参加させる。

```bash
docker network connect ros2-lab-net kali-vnc      # 接続
docker network disconnect ros2-lab-net kali-vnc   # 切断
```

接続先コンテナの既存ネットワークはそのまま残り、NIC が 1 本追加されるだけ。
コンテナを作り直すと接続は外れる。

疎通確認（追加インストール不要）:

```bash
# 名前解決
docker exec kali-vnc getent hosts ros2lab
docker exec ros2lab getent hosts kali-vnc

# TCP: ros2lab で待ち受け、kali-vnc から接続
docker exec -d ros2lab python3 -m http.server 8000
docker exec kali-vnc bash -c 'echo > /dev/tcp/ros2lab/8000 && echo OK || echo NG'
docker exec ros2lab pkill -f 'http.server 8000'
```

ネットワークは `docker compose up` 時に作成される。`kali-vnc` が接続中に
`docker compose down` するとネットワーク削除が失敗するため、先に切断する。

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
