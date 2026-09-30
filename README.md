# ros2-poc

ROS 2 (Jazzy Jalisco) の Pub/Sub・Discovery を、実機や既存の ROS グラフに
一切触れずローカルで安全に試すための隔離実習環境。Colima 上の Docker
コンテナとして立ち上げる。

あわせて、ブラウザ（noVNC）から Gazebo Harmonic / MoveIt 2 / RViz2 で
ロボットアームを動かすシミュ用コンテナ `ros2arm` も用意している
（[アームシミュ](#アームシミュros2arm--novnc) 参照）。

## 前提

- [Colima](https://github.com/abiosoft/colima)（`colima start`）
- Docker CLI / Docker Compose v2（`docker compose`）

## 起動

```bash
colima start   # 未起動の場合
docker compose up -d --build
```

コンテナに入ると `ROS_DOMAIN_ID=42` / `ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET`
が自動的に有効になる（`ros2lab` エイリアスとして `.bashrc` に設定済み）。
Discovery は同じ Docker ネットワーク（`ros2-lab-net` 上のラボ用コンテナ等）
までに限られ、ホスト上の他の ROS グラフやネットワーク越しの実機とは混線しない。

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

## アームシミュ（ros2arm / noVNC）

Factory I/O（Windows 専用）の代わりに、Mac / Linux のブラウザだけで
ROS 2 のロボットアーム制御を試すためのコンテナ。
[tiryoh/ros2-desktop-vnc](https://github.com/Tiryoh/docker-ros2-desktop-vnc)
（Ubuntu 24.04 デスクトップ + noVNC、Gazebo Harmonic 同梱）に以下を追加している。

| 追加物 | 用途 |
|---|---|
| MoveIt 2 | 衝突回避・軌道計画 |
| `moveit_resources_panda_moveit_config` | Panda アームの MoveIt + RViz デモ |
| `ur_simulation_gz` / `ur_moveit_config` | UR アームの Gazebo シミュ |
| CRANE-X7（`/opt/crane_ws`） | アールティ製アームの Gazebo + MoveIt（ソースビルド、コミット固定） |

重いので compose の profile `arm` を指定したときだけ起動する。
`docker compose up -d` だけなら従来どおり `ros2lab` のみが起動する。

### 起動・停止

```bash
docker compose --profile arm up -d --build   # 初回ビルドは 10 分前後
```

ブラウザで <http://127.0.0.1:6080/> を開き、「Connect」でデスクトップに入る。
ポートは `127.0.0.1` にだけ公開しているので、LAN の他端末からは見えない。

```bash
docker compose --profile arm down   # ros2lab も含めて停止・削除
docker compose stop ros2arm         # ros2arm だけ一時停止（start で再開、コンテナ内の状態は残る）
```

`down` するとコンテナ内の変更は消える。残したいファイルは `/workspace` に置く。
`kali-vnc` を `ros2-lab-net` に繋いでいる場合は、先に切断してから `down` する
（[他のラボ用コンテナとの接続](#他のラボ用コンテナとの接続ros2-lab-net) 参照）。

### デモ

noVNC のデスクトップで端末（Terminator または MATE Terminal）を開いて実行する。
CPU 描画で重いため、デモは **1度に1つ** だけ起動する（Ctrl+C で終了してから次へ）。

```bash
# 1. Panda + MoveIt: RViz の MotionPlanning パネルで Plan & Execute
ros2 launch moveit_resources_panda_moveit_config demo.launch.py

# 2. CRANE-X7 を Gazebo 上に表示（MoveIt / RViz も同時に起動する）
ros2 launch crane_x7_gazebo crane_x7_with_table.launch.py

# 3. UR を Gazebo 上に表示
ros2 launch ur_simulation_gz ur_sim_control.launch.py
```

### ros2lab から ros2arm のアームを動かす（2コンテナ構成）

`ros2arm`（シミュ = サーバー側）と `ros2lab`（クライアント側）は `ros2-lab-net` 上で
同じ `ROS_DOMAIN_ID=42` / `SUBNET` 設定なので、互いのトピックが見える。
デモ 2（CRANE-X7）を起動した状態で、Mac のターミナルから:

```bash
# ros2arm 側のトピックが ros2lab から見えることを確認（出ない場合は数秒待って再実行）
docker compose exec ros2lab bash -lc 'ros2 topic list | grep -E "joint_states|crane_x7_arm_controller"'

# 関節角の指令を送る（Gazebo 上のアームが 3 秒かけて動く）
docker compose exec ros2lab bash -lc "ros2 topic pub --once /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory \"{joint_names: [crane_x7_shoulder_fixed_part_pan_joint, crane_x7_shoulder_revolute_part_tilt_joint, crane_x7_upper_arm_revolute_part_twist_joint, crane_x7_upper_arm_revolute_part_rotate_joint, crane_x7_lower_arm_fixed_part_joint, crane_x7_lower_arm_revolute_part_joint, crane_x7_wrist_joint], points: [{positions: [0.5, 0.3, 0.0, -1.2, 0.0, -0.5, 0.0], time_from_start: {sec: 3}}]}\""

# 現在の関節角を読む
docker compose exec ros2lab bash -lc 'ros2 topic echo --once /joint_states'
```

`trajectory_msgs` は ros-base に含まれるので、`ros2lab` 側に追加インストールは不要。

### 注意

- **リソース**: Colima は 4CPU / 8GiB / ディスク 50GB 程度を推奨
  （`colima start --cpu 4 --memory 8 --disk 50`）。イメージは約 13GB になる。
- **noVNC の認証**: VNC パスワードは設定していない。LAN からは見えないが、同じ Mac のブラウザで
  開いた Web ページからは `127.0.0.1:6080` に接続を試みられる。使うときだけ起動し、終わったら止める。
  `ros2-lab-net` に繋いだ他のラボ用コンテナ（kali-vnc 等）からも `ros2arm:80` に到達できる。
- **`/workspace` の所有者**: `ros2lab` は root、`ros2arm` のデスクトップは `ubuntu` ユーザーで動くため、
  片方で作ったファイルをもう片方から書き換えられないことがある。その場合は `ros2arm` 側で `sudo` を使う
  （`ubuntu` ユーザーはパスワードなしで sudo できる）。
- **ディスク**: ビルドキャッシュが溜まったら `docker builder prune` で掃除する。
- **ライセンス**: CRANE-X7 のモデル（`crane_x7_description`）は株式会社アールティの
  非商用ライセンス。学習・研究目的に限って使う。
- **GPU 直結への移行**: noVNC は CPU 描画のため遅延がある。重いシミュが必要になったら、
  Linux + NVIDIA GPU のホストで X11 / Wayland を共有し `--gpus all`（NVIDIA Container Toolkit）
  で動かす構成へ移行する。ROS 2 のコード自体はそのまま使える。
- **実機への移行**: シミュで書いたノードは、トピック名・コントローラ名を実機ドライバ
  （CRANE-X7 なら `crane_x7_control`）に合わせれば実機でも動く。

## SROS2 について

`ros-jazzy-sros2`（DDS-Security による認証・アクセス制御・暗号化のツール群）
を同梱済み。今回の Pub/Sub 実習では使用しないが、将来 SROS2 を使った隔離
実習を行う際にイメージの再ビルドが不要になるようにしている。

```bash
docker compose exec ros2lab bash -lc 'ros2 pkg list | grep sros2'
```

## 片付け

```bash
docker compose --profile arm down  # ros2lab と ros2arm を停止・削除
```

`./workspace` は両コンテナの `/workspace` にマウントされる。SROS2 の
keystore など、永続化したいファイルはここに置く。
