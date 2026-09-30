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

`ros2lab-a` と `ros2lab-b` の 2 コンテナが同じイメージから立ち上がる。
Discovery 範囲が `SUBNET` なので、コンテナをまたいだ Pub/Sub を試せる。

ターミナルA（ros2lab-a で Subscriber）:

```bash
docker compose exec ros2lab-a bash
ros2 topic echo /chatter std_msgs/msg/String
```

ターミナルB（ros2lab-b で Publisher）:

```bash
docker compose exec ros2lab-b bash
ros2 topic pub /chatter std_msgs/msg/String "{data: 'hello'}" -r 1
```

Discovery の状態を覗く:

```bash
docker compose exec ros2lab-a bash
ros2 topic list -t
ros2 topic info /chatter -v
```

環境の自己診断:

```bash
docker compose exec ros2lab-a bash -lc 'ros2 doctor --report | sed -n "1,40p"'
```

## 他のラボ用コンテナとの接続（ros2-lab-net）

`ros2lab-a` / `ros2lab-b` は `ros2-lab-net`（internal: インターネットへの出口なし）にも参加している。
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
docker exec kali-vnc getent hosts ros2lab-a
docker exec ros2lab-a getent hosts kali-vnc

# TCP: ros2lab-a で待ち受け、kali-vnc から接続
docker exec -d ros2lab-a python3 -m http.server 8000
docker exec kali-vnc bash -c 'echo > /dev/tcp/ros2lab-a/8000 && echo OK || echo NG'
docker exec ros2lab-a pkill -f 'http.server 8000'
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

#### Mac のターミナルから起動する

noVNC の端末を使わず、Mac のターミナルから `docker exec` で起動することもできる。
ウィンドウは noVNC（<http://127.0.0.1:6080/>）側のデスクトップに表示される。

```bash
# シェルに入る（以降は上のコマンドをそのまま実行できる）
docker exec -it -u ubuntu -e DISPLAY=:1 ros2arm bash

# 1コマンドで起動する（例: デモ 2）
docker exec -it -u ubuntu -e DISPLAY=:1 ros2arm bash -ic 'ros2 launch crane_x7_gazebo crane_x7_with_table.launch.py'
```

| オプション | 理由 |
|---|---|
| `-u ubuntu` | ROS 2 の環境設定は `/home/ubuntu/.bashrc` にある（root では読まれない） |
| `-e DISPLAY=:1` | VNC の画面 `:1` に GUI を表示する |
| `bash -ic` | 対話シェルでないと `.bashrc` が読まれず `ros2` が見つからない |

### ros2lab から ros2arm のアームを動かす（2コンテナ構成）

`ros2arm`（シミュ = サーバー側）と `ros2lab`（クライアント側）は `ros2-lab-net` 上で
同じ `ROS_DOMAIN_ID=42` / `SUBNET` 設定なので、互いのトピックが見える。
デモ 2（CRANE-X7）を起動した状態で、Mac のターミナルから:

```bash
# ros2arm 側のトピックが ros2lab から見えることを確認（出ない場合は数秒待って再実行）
docker compose exec ros2lab bash -lc 'ros2 topic list | grep -E "joint_states|crane_x7_arm_controller"'   # 数秒かかる

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
- **実機への移行**: シミュと実機でコントローラ名は同じ
  （`/crane_x7_arm_controller/joint_trajectory` 等）。ros2lab のコードは変えずに、
  ドライバ側だけ ros2arm から ros2real に差し替えれば実機でも動く見込み。
  手順は次の「実機で動かす（ros2real）」を参照。

## 実機で動かす（ros2real / CRANE-X7）

> **実機は未検証**: イメージのビルド、`docker compose config`、モックのハードウェア
> （`use_mock_components:=true`）でのドライバ起動までを確認済み。実機での動作（udev rule、
> `latency_timer`、トルクの挙動）は未確認。初回は下の「安全上の注意」を必ず読むこと。

```
ros2lab-a/b ──ros2-lab-net (DDS)── ros2real ── /dev/crane_x7 ── CRANE-X7
 (クライアント)                     (crane_x7_control のみ)
```

`ros2real` は実機ドライバ専用のコンテナ（`Dockerfile.real`、`profile: real`）。
クライアントの `ros2lab` は変更せず、`ros2-lab-net` 越しの Discovery で実機に指令する。

### 前提

- **Linux ホスト（Ubuntu など）が必須**。Mac + Colima では USB をコンテナに渡せない。
- CRANE-X7 を USB で接続し、Docker が使えること。

### 1. ホストの udev rule を入れる

```bash
lsusb   # CRANE-X7 の USB-シリアル変換の ID（例: 0403:6014）を確認する
```

`host/99-crane-x7.rules` の `idProduct`（既定 `6014`、FTDI 想定）を `lsusb` の値に合わせてから配置する。

```bash
sudo cp host/99-crane-x7.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
# USB を挿し直して確認
ls -l /dev/crane_x7                                        # ttyUSB* への symlink
cat /sys/bus/usb-serial/devices/$(basename "$(readlink -f /dev/crane_x7)")/latency_timer  # 1 になっていること
```

`/dev/crane_x7` が作られない場合は `udevadm info -a -n /dev/ttyUSB0` で ID を確認し直す
（他の USB-シリアルを挿していると番号が `ttyUSB1` などになる）。

`latency_timer` は 200Hz 制御のために 1ms へ下げる設定で、コンテナ内の `/sys` は書けないためホスト側で行う。

### 2. 起動する

`ros2arm`（シミュ）と `ros2real` は**同時に起動しない**。同名のコントローラが二重になり、
ros2lab の指令が実機にも届いてしまう。`scripts/up-real.sh` は `ros2arm` が起動中なら拒否する。

```bash
docker compose --profile arm down   # ros2arm を止める（起動していれば）
bash scripts/up-real.sh             # ros2real を起動（ドライバはまだ動かない）

# ドライバを手動起動（サーボがトルクオンし、腕が現在姿勢を保持する）
docker compose exec ros2real bash -lc \
  'ros2 launch crane_x7_control crane_x7_control.launch.py port_name:=/dev/crane_x7'
```

`/dev/crane_x7` が無いと `up-real.sh` は起動せずに理由を表示する。`docker compose --profile real up`
を直接叩くと上のガードを通らないため、使わない。USB を抜き差ししたらコンテナの
デバイスが古くなるので、ドライバを止めて `bash scripts/up-real.sh` を実行し直す。

**実機なしでドライバだけ確認する**（モックのハードウェアで起動する）:

```bash
docker compose exec ros2real bash -lc \
  'ros2 launch crane_x7_control crane_x7_control.launch.py use_mock_components:=true'
```

### 3. ros2lab から指令する

ドライバを起動したまま別ターミナルで、シミュと同じコマンドを使う
（「ros2lab から ros2arm のアームを動かす」参照）。

```bash
docker compose exec ros2lab bash -lc 'ros2 topic list | grep -E "joint_states|crane_x7_arm_controller"'   # 数秒かかる
docker compose exec ros2lab bash -lc 'ros2 topic echo --once /joint_states'
```

### 安全上の注意

- 実機は指令どおりに動く。初回は**小さな振幅・長い `time_from_start`** で試し、周囲を空けておくこと。
- **ドライバが止まるとトルクが抜けて腕が自重で落ちる**。次のいずれでも起きる:
  ドライバの停止（Ctrl+C）、`docker compose down`、端末や SSH の切断、コンテナの異常終了、USB の抜け。
  止める前に腕を安全な姿勢（低い位置・何も持たない状態）へ戻す。
- **`ros2-lab-net` に参加する全コンテナ（ros2lab、kali-vnc など）が実機に指令できる**
  （DDS は無認証）。実機を動かす間は、信頼できないコンテナをこのネットワークに参加させない。
  SROS2 での制限は未対応。
- `ros2arm` と `ros2real` の同時起動は `up-real.sh` が一方向だけ防ぐ。`ros2real` の起動中に
  `--profile arm up` を叩くと二重になるので、シミュを使う前に `ros2real` を止める。
- `ros2real` に restart ポリシーは付けていない（再起動後に勝手にトルクオンさせないため）。
- `ros2real` のコンテナは `dialout` グループに所属する。ホストの `dialout` の GID が 20 でない場合は
  `docker-compose.yml` の `group_add` に GID を数値で指定する。
- 通信が不安定なら、Dynamixel Wizard 2 でサーボの Return Delay Time を下げる（既定 250 = 500μs）。
- モデル（`crane_x7_description`）は株式会社アールティの非商用ライセンス。学習・研究目的に限る。

## SROS2 について

`ros-jazzy-sros2`（DDS-Security による認証・アクセス制御・暗号化のツール群）
を同梱済み。今回の Pub/Sub 実習では使用しないが、将来 SROS2 を使った隔離
実習を行う際にイメージの再ビルドが不要になるようにしている。

```bash
docker compose exec ros2lab-a bash -lc 'ros2 pkg list | grep sros2'
```

## 片付け

```bash
docker compose --profile arm down  # ros2lab と ros2arm を停止・削除
docker compose --profile real down  # ros2lab と ros2real を停止・削除（先にドライバを止め腕を安全な姿勢へ）
```

`./workspace` は両コンテナの `/workspace` にマウントされる。SROS2 の
keystore など、永続化したいファイルはここに置く。
