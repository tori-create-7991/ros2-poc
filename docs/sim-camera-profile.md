# カメラプロファイル: Gazebo の仮想カメラで実カメラを模擬する

`ros2arm` の Gazebo に仮想カメラ（RGB-D）と掴める物体（青い 4cm 立方体）を足し、**実カメラ（usb_cam / RealSense D435）と同じトピック名・型・エンコーディング・フレーム**で画像を出す。
実カメラに替えても、受け取る側（`ros2lab` のスクリプト、将来の VLA ノード）のコードを変えずに済むようにするための層で、`ros2arm` → `ros2real` の差し替え（ハードウェア層だけ入れ替える）と同じ思想。

- 実カメラは未購入で、Mac + Colima は USB をコンテナに渡せないため、当面はシミュのみ。
- 公式の `crane_x7_ros`（コミット固定）は書き換えない。`/opt/crane_ws` の上に ROS 2 パッケージ `ros2_poc_sim`（[../ros2_poc_sim](../ros2_poc_sim)）を overlay している。
- 調査・設計の背景は `.research/sim-camera-profile_20261002/` と `.plans/sim-camera-profile/design.md`（git 管理外）。

## 使い方

`ros2arm` の noVNC デスクトップで端末を開いて実行する（`docker compose exec ros2arm bash` ではなく、デスクトップ上の端末。Gazebo の GUI を出すため）。

```bash
# 公式の Gazebo + MoveIt + RViz に、仮想カメラと青い立方体を足して起動（公式 launch は無改変）
ros2 launch ros2_poc_sim arm_with_camera.launch.py profile:=realsense_d435

# 既に公式 launch が動いているところへ、後から足す場合
ros2 launch ros2_poc_sim sim_camera.launch.py profile:=realsense_d435 placement:=fixed_near_top
```

| 引数 | 既定 | 内容 |
|---|---|---|
| `profile` | `realsense_d435` | `realsense_d435` / `usb_cam` |
| `placement` | `fixed_near_top` | `fixed_near_top`（机の真上、物体まで約 0.4m。公式 `color_detection` 用）/ `fixed_front_oblique`（ロボットの正面、三人称視点） |
| `pointcloud` | `false` | 点群（`/camera/depth/color/points`）も出す。重いので既定は off |
| `compressed` | `true` | `<画像トピック>/compressed`（JPEG）も出す |
| `ns_mode` | プロファイルの既定 | `nested` にすると素の realsense-ros Jazzy と同じ `/camera/camera/...` 形になる |
| `spawn_object` | `true` | 青い立方体を置く |

再実行しても Sensors システムの二重追加やモデルの重複は起きない（`sim_camera_*` モデルの有無で判断する）。別のプロファイルを後から足すこともできる。

### 出るトピック

| プロファイル | トピック | 型・エンコーディング | frame_id |
|---|---|---|---|
| `realsense_d435` | `/camera/color/image_raw`、`/camera/color/camera_info` | Image `rgb8`、CameraInfo | `camera_color_optical_frame` |
| | `/camera/aligned_depth_to_color/image_raw`、`.../camera_info` | Image **`16UC1`（mm、無効は 0）** | `camera_color_optical_frame` |
| | `/camera/depth/color/points`（`pointcloud:=true`） | PointCloud2（光学座標） | `camera_color_optical_frame`（実機は `camera_depth_optical_frame`。下記） |
| | `/camera/color/image_raw/compressed` | CompressedImage（JPEG） | |
| `usb_cam` | `/image_raw`、`/camera_info`、`/image_raw/compressed` | Image `rgb8`、CameraInfo | `camera` |

TF は静的に出る: `base_link → camera_link（realsense）/ usb_cam_link（usb_cam）→ …光学フレーム`。

## ros2lab から使う

`ros2lab-a` / `ros2lab-b` は `ros2arm` と同じ DDS ドメインにいるので、compose の変更なしでトピックが見える（Discovery に 10〜20 秒かかる）。`ros2lab` には `image_transport`・`cv_bridge`・`rqt_image_view` が入っていないため、次のどれかで確認する。

```bash
# 流れているか・レート・帯域（別コンテナ間は圧縮して流すのが現実的）
docker compose exec ros2lab-a bash -lc 'ros2 topic hz /camera/color/image_raw/compressed'
docker compose exec ros2lab-a bash -lc 'ros2 topic echo --once --field encoding /camera/aligned_depth_to_color/image_raw'

# 画像を PNG で保存して目で確認（ホストの ./workspace/ に出る）
docker compose cp ros2_poc_sim/scripts/save_frame.py ros2lab-a:/tmp/save_frame.py
docker compose exec ros2lab-a bash -lc 'python3 /tmp/save_frame.py /camera/color/image_raw /workspace/color.png'

# ノード・スクリプトの動作確認: 青い立方体を検出して TF target_0 を出す（VLA の代わりの簡易スクリプト。アームは動かさない）
docker compose cp ros2_poc_sim/scripts/vla_stub_detect.py ros2lab-a:/tmp/vla_stub_detect.py
docker compose exec ros2lab-a bash -lc 'python3 /tmp/vla_stub_detect.py'
```

`ros2arm` のデスクトップ上なら `rqt_image_view` でも見られる。

### 簡易検出スクリプト（`vla_stub_detect.py`）

OpenVLA の代わりの簡易スクリプト。色画像・整列深度・camera_info から青い立方体を検出し、TF **`target_0`**（親 = 画像の `frame_id`）を配信する。公式 `crane_x7_examples` の `color_detection` と同じ規約（青の HSV 範囲、有効距離 0.2〜0.5m、`target_0`）なので、後段の `pick_and_place_tf` や本物の VLA ノードと差し替えられる。`ros2lab`（OpenCV なし）で動くよう numpy だけで書いてある。

- 受け取るのは `rgb8` の色画像と `16UC1`(mm) の深度。実機でもシミュでも同じ（これが「受け手無改変」の確認）。
- `base_link` から見た位置が分かれば INFO に出す（例: `base_link=(0.200,0.101,0.025)`）。
- アームには何も送らない。

公式の `color_detection` も、`camera_example.launch.py` ではなく単独起動なら、そのまま動く（`camera_example.launch.py` は `pick_and_place_tf` も起動してアームが動くので使わない）:

```bash
ros2 run crane_x7_examples color_detection --ros-args -p use_sim_time:=true
ros2 run tf2_ros tf2_echo base_link target_0
```

## 検証

```bash
# ros2arm で arm_with_camera.launch.py を起動したまま、ホストで実行（アームは動かさない）
bash ros2_poc_sim/scripts/verify_sim_camera.sh realsense_d435   # または usb_cam
```

V1（pytest）、V3（ros2lab から見える）、出力契約（型・エンコーディング・frame_id・Hz・同一 stamp・TF）、画像の保存、簡易検出スクリプトの位置誤差（±1.5cm）を順に確認する。契約ファイルは [../ros2_poc_sim/config/contracts](../ros2_poc_sim/config/contracts)。**同じ契約を実機にも流せる**（`--min-hz 5` などで Hz の下限を厳しくする）。

## プロファイルの形式（YAML）

[../ros2_poc_sim/config/profiles](../ros2_poc_sim/config/profiles) に置く。どの Gazebo ストリームを、どの名前・型・エンコーディング・フレームで出すかを宣言する。主なキー:

| キー | 内容 |
|---|---|
| `based_on` | 模擬対象のドライバと版、出典、`verified`（`source-read` = ソース読み、`measured` = 実機で確認） |
| `sensor` | `gz_type`（`camera` / `rgbd_camera`）、解像度、`fps`（上限）、`clip` |
| `intrinsics` | `fx, fy, cx, cy`。**Gazebo が描画できる理想ピンホール（`fx=fy`、主点が中心、歪みゼロ）に限る**。違う値は検証で拒否する（CameraInfo が嘘になるため）。`provisional: true` は実機未校正 |
| `frames` / `tf` | フレーム名と静的 TF |
| `topics.streams` | `source`・`name`・`type`・`encoding`・`frame`・`transform`（変換）・`enabled_by` |
| `qos`、`compressed`、`not_reproduced` | QoS、圧縮して出すストリーム、再現しないもの |

変換（`transform`）は `passthrough` / `depth_to_16uc1_mm`（32FC1(m) → 16UC1(mm)）/ `points_to_optical`（点群をセンサ座標から光学座標へ）。新しいカメラは YAML と契約ファイルを足し、足りない変換があれば [`transforms.py`](../ros2_poc_sim/ros2_poc_sim/transforms.py) に単体テスト付きで足す。

## 既知の差分（実機と違う点）

- **fps は低い**。CPU 描画（llvmpipe）のため。実測（Colima 4 CPU / 8GB、640x480、要求 10Hz）: ロボット・MoveIt・RViz・Gazebo GUI 同居時は RTF 0.22、`ros2lab` での受信は **生画像で 1〜2Hz、JPEG 圧縮で約 2Hz**、ロボットなしの Gazebo 単体なら 6〜9Hz。実機は 30Hz 級。レートを前提にする受け手は、シミュと実機で違うことを許容する必要がある。
- **内部パラメータは暫定**（`provisional: true`）。D435 の `fx=462.14` は公称 HFOV 69.4° からの逆算で、640x480 の実効値は実機入手後に差し替える。歪み・主点ずれ・`fx≠fy` は再現できない。
- **点群の `frame_id`**: 実機 RealSense は `camera_depth_optical_frame`、シミュは `camera_color_optical_frame`。Gazebo は 1 つの視点から深度を色に位置合わせして描画するため、実機ラベルを付けると 15mm ずれる。位置の正しさを優先している。
- **画像の圧縮サイズ**は机上のシーンで約 58KB/枚（単色の合成シーンでの 9.5KB より大きい）。
- 固定カメラは **シミュ上の幽霊**（衝突なし、MoveIt の計画シーンにも載らない）。

## 再現できないもの

| 項目 | 理由 |
|---|---|
| 露出・ゲイン・オートフォーカス・ホワイトバランス（`enable_auto_exposure`、V4L2 制御、pylon の `set_exposure` 等） | Gazebo に対応する機構がない |
| USB の挙動（帯域、切断・再接続、起動遅延、デバイス未検出の待ち） | シミュには USB がない |
| 実機のノイズ・深度の欠け・飛び・フライングピクセル・反射面 | 理想センサ（`noise: null`） |
| D435i の IMU、infra1/infra2、`depth/image_rect_raw`（非整列深度）、metadata、extrinsics | 第 1 弾の対象外 |
| ローリングシャッター、レンズフレア、照明変化 | レンダラが対象外 |

## 隔離方針との関係

- 仮想カメラのトピックは `ros2arm` が参加する既存の経路（`default` と `ros2-lab-net`）で `ros2lab` に届く。compose の変更は不要。
- DDS は無認証。`ros2-lab-net` に `kali-vnc` などを接続すると、そこからカメラトピックの購読や偽の画像の注入ができる（シミュ限定なので実害は小さいが、将来 VLA の入力を汚染しうる）。
- 実カメラを接続するときは `ros2real` 側で動かし、画像を `ros2-lab-net` に出さない構成を維持する（`ros2real` は `lab` に繋がない）。

## トラブルシュート

| 症状 | 原因・対処 |
|---|---|
| `ros2lab` にトピックが出ない | Discovery に 10〜20 秒かかる。`ros2arm` で launch が動いているか、同じ `ROS_DOMAIN_ID`（42）かを確認 |
| `gz_setup` が `ワールド default のサービスが立たない` で終わる | 公式 launch を先に起動していない、または Fuel のモデルダウンロード待ち（外向き HTTP が必要）。待ってから `sim_camera.launch.py` を再実行 |
| 画像が灰色一色 | 古い版では `ros_gz_sim create` が SDF の姿勢を無視していた。最新の `ros2_poc_sim` を使う（姿勢を `-x -y -z -R -P -Y` で明示している） |
| 再起動後に二重に描画される | Gazebo を止めずに `sim_camera.launch.py` を何度も起動しても二重にならない。Gazebo ごと再起動したときは `sim_camera_*` モデルも消えるので、そのまま起動し直してよい |
