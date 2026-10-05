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
| `pointcloud` | `false` | 点群（`/camera/depth/color/points`）も出す。**未検証**（検証 V9 未実施）。1 枚 7.4MB で CPU 描画下では重く、Colima の 8GB ではメモリが逼迫しうる。QoS は RELIABLE・depth 2（公式 `point_cloud_detection` が RELIABLE で購読するため best_effort にはできない）。既定は off |
| `compressed` | `true` | `<画像トピック>/compressed`（JPEG）も出す |
| `ns_mode` | プロファイルの既定 | `nested` にすると素の realsense-ros Jazzy と同じ `/camera/camera/...` 形になる |
| `spawn_object` | `true` | 青い立方体を置く |
| `world` | `default` | Gazebo のワールド名 |
| `wait_sec` / `spawn_timeout` | `300` / `240` | ワールドが立つまで待つ秒数 / 1 回のスポーンのタイムアウト。初回（Fuel のモデル取得）やホストが高負荷のときは Gazebo の起動自体が数分から 7 分以上かかることがある（実測）。**足りないときは先に公式 launch だけを起動し、立ち上がってから `sim_camera.launch.py` を起動するか、`wait_sec` を延ばす** |
| `fail_fast` | `false` | `gz_setup` が失敗したときに launch 全体を止める。既定は **このプロファイルのノードだけを止め**、公式の Gazebo / MoveIt は巻き込まない |

**再実行の挙動**

- **Gazebo 側は冪等**。Sensors システムは「追加した直後に置く目印のモデル（`sim_camera_sensors_marker`）」で追加済みかを判断し、カメラ・物体は名前で重複を避ける。Gazebo は Sensors システムの有無を問い合わせる手段を持たないための方式。目印は Gazebo を再起動すると消えるので、**Gazebo を再起動したら `sim_camera.launch.py` も起動し直す**（bridge / adapter が動いたままでも、カメラのスポーンは起動時にしか行わない）。状態を取得できないとき（`gz` が重すぎる等）は、推測で追加せず中止する。
- **ROS ノード（bridge / adapter / republish）は冪等ではない**。同じプロファイルを動かしたまま `sim_camera.launch.py` を再実行しない（先に Ctrl-C する）。ノード名・republish 名はプロファイル名を含むので、**別のプロファイルを同時に動かすのは問題ない**（TF のフレーム名も重ならないよう分けてある）。bridge と adapter は落ちたら 2 秒後に再起動する（`respawn`）。republish は監視していない。
- **`gz_setup` が失敗（rc≠0）したら、このプロファイルの bridge / adapter / republish だけを止める**（公式の Gazebo / MoveIt は動いたまま）。ログに終了コードを出す。止まった後は `ros2 launch ros2_poc_sim sim_camera.launch.py ...` を**単独で再実行**すればよい（ノードが二重にならない）。`arm_with_camera.launch.py` から起動していた場合も同じで、公式側はそのまま動く。`fail_fast:=true` なら launch 全体を止める。
- 生成物（SDF・ブリッジ設定）は `/tmp/ros2_poc_sim_<uid>/<プロファイル名>/`（0700）に出る。困ったときはここを見る。

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

**イメージの再ビルド**: `ros2_poc_sim/` は `Dockerfile.arm` の末尾でイメージに焼かれる（`--symlink-install` ではない）。ソースやプロファイルを編集したら `docker compose --profile arm build ros2arm` で再ビルドする（変更が最終 2 レイヤだけなので速い）。`docker exec` の非対話シェルは `~/.bashrc` を読まないので、`source /opt/ros2_poc_ws/install/setup.bash` を明示する。

### 簡易検出スクリプト（`vla_stub_detect.py`）

OpenVLA の代わりの簡易スクリプト。色画像・整列深度・camera_info から青い立方体を検出し、TF **`target_0`**（親 = 画像の `frame_id`）を配信する。公式 `crane_x7_examples` の `color_detection` と同じ規約（青の HSV 範囲、有効距離 0.2〜0.5m、`target_0`）なので、後段の `pick_and_place_tf` や本物の VLA ノードと差し替えられる。`ros2lab`（OpenCV なし）で動くよう numpy だけで書いてある。

- 受け取るのは `rgb8` の色画像と `16UC1`(mm) の深度。実機でもシミュでも同じ（これが「受け手無改変」の確認）。深度が無い `usb_cam` プロファイルでは、10 秒たっても深度が来ないとエラーを出す。
- 入力は信頼しない（DDS は無認証）: 色・深度の `frame_id` が `camera_color_optical_frame` でない、色と深度の大きさが違う、`camera_info` の `fx, fy` が不正（0・負・NaN）、位置が横方向 ±1m を超える、のいずれかなら破棄し、1 つの不正な組で落ちない。色と深度は stamp が同じ組だけを使い、どちらが先に着いても組になる。
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

V1（pytest。本番の DDS ドメインに混ざらないよう `ROS_DOMAIN_ID=97` で実行し、終了コードで判定）、V3（ros2lab から見える）、出力契約（型・エンコーディング・frame_id・Hz・同一 stamp・CameraInfo の内容・TF）、画像の保存（ほぼ単色なら不合格）、簡易検出スクリプトの位置誤差（±1.5cm）を順に確認する。

`pick_and_place_tf` が動いている間は V7b を実行しない（`target_0` を流すとアームが動く）。スクリプトは `ros2 node list` で検出して不合格で中止し、ノード一覧を取れないときも安全側（中止）に倒す。名前は公式 `camera_example.launch.py` の `Node(name='pick_and_place_tf')`。Discovery 前の取りこぼしや、確認から実行までの隙間は残るので、**実機（`ros2real`、同じ DDS ドメイン 42）が動いている環境では実行しない**。

自動化していないもの（手で確認）: 公式 `color_detection`（V7、上の単独起動）、ロボットの位置（V2）、点群（V9）、TF の値（連結だけ確認）。V1 が見るのはイメージに焼かれたコピーなので、手元のソースとイメージ内のコピーが違えば（編集後に再ビルドしていなければ）不合格にする。Hz の下限は「生きている」ことの確認（0.5Hz）で、実機に流すときは `--min-hz` で厳しくする。契約ファイルは [../ros2_poc_sim/config/contracts](../ros2_poc_sim/config/contracts)。**同じ契約を実機にも流せる**（`--min-hz 5` などで Hz の下限を厳しくする）。

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

**プロファイルを書くときの注意**: `frames` は「論理名 → 実フレーム名」の表で、ストリームの `frame` と `tf.gz_sensor_frame` は論理名（キー）、`tf.links` の `parent` / `child` は実フレーム名で書く。`gz` の色は `rgb8` なので、変換なしで他のエンコーディングを名乗ることは検証で拒否される。

変換（`transform`）は `passthrough` / `depth_to_16uc1_mm`（32FC1(m) → 16UC1(mm)）/ `points_to_optical`（点群をセンサ座標から光学座標へ）。新しいカメラは YAML と契約ファイルを足せば増やせる（契約とプロファイルの食い違いは pytest が検出する）。次のものは **YAML だけでは足りない**: 新しい変換（`bgr8` / `bgra8` / `mono8` への変換など。[`transforms.py`](../ros2_poc_sim/ros2_poc_sim/transforms.py) に単体テスト付きで足し、`camera_adapter.py` の呼び出しも足す）、非理想の内部パラメータ（Gazebo が描画できない）、1 プロファイルに複数のカメラ（ステレオ・IR など）。

## 既知の差分（実機と違う点）

- **fps は低い**。CPU 描画（llvmpipe）のため。実測（Colima 4 CPU / 8GB、640x480、要求 10Hz）: ロボット・MoveIt・RViz・Gazebo GUI 同居時は RTF 0.22、`ros2lab` での受信は **生画像で 1〜2Hz、JPEG 圧縮で約 2Hz**、ロボットなしの Gazebo 単体なら 6〜9Hz。実機は 30Hz 級。レートを前提にする受け手は、シミュと実機で違うことを許容する必要がある。
- **内部パラメータは暫定**（`provisional: true`）。D435 の `fx=462.14` は公称 HFOV 69.4° からの逆算で、640x480 の実効値は実機入手後に差し替える。歪み・主点ずれ・`fx≠fy` は再現できない。
- **点群の `frame_id`**: 実機 RealSense は `camera_depth_optical_frame`、シミュは `camera_color_optical_frame`。Gazebo は 1 つの視点から深度を色に位置合わせして描画するため、実機ラベルを付けると 15mm ずれる。位置の正しさを優先している。
- **画像の圧縮サイズ**は机上のシーンで約 58KB/枚（単色の合成シーンでの 9.5KB より大きい）。
- 固定カメラは **シミュ上の幽霊**（衝突なし、MoveIt の計画シーンにも載らない）。
- `contract` の `camera_info` の `fx` はプロファイルの暫定値に固定している。実機に流すときは外すか実測値に差し替える。

## 再現できないもの

| 項目 | 理由 |
|---|---|
| 露出・ゲイン・オートフォーカス・ホワイトバランス（`enable_auto_exposure`、V4L2 制御、pylon の `set_exposure` 等） | Gazebo に対応する機構がない |
| USB の挙動（帯域、切断・再接続、起動遅延、デバイス未検出の待ち） | シミュには USB がない |
| 実機のノイズ・深度の欠け・飛び・フライングピクセル・反射面 | 理想センサ（`noise: null`） |
| D435i の IMU、infra1/infra2、`depth/image_rect_raw`（非整列深度）、metadata、extrinsics | 第 1 弾の対象外 |
| ローリングシャッター、レンズフレア、照明変化 | レンダラが対象外 |

## `gz_setup` の終了コード

| rc | 意味 | そのまま再実行してよいか |
|---|---|---|
| 0 | 成功 | — |
| 2 | ワールド（`/world/<w>/entity/system/add`）が `wait_sec` 以内に立たない。公式 launch 未起動、Gazebo の起動が遅い（初回の Fuel 取得・高負荷）、`GZ_PARTITION` / `GZ_IP` の食い違い。ログに最後の `gz` の失敗理由と環境変数を出す | はい（Gazebo の状態は変えていない） |
| 3 | Sensors システムの追加を確認できない、または目印のモデルを確認できない（**追加の直後から目印の確認までの間に Ctrl-C / SIGTERM した場合も同じ状態**になり、ログに案内が出る）。**追加済みかもしれず、再実行すると二重になりうる** | **いいえ。Gazebo を再起動してから** |
| 4 | カメラのスポーンに失敗（Sensors と目印は作成済み） | はい |
| 5 | 物体のスポーンに失敗（カメラは作成済み） | はい |
| 6 | ワールドの状態（`scene/info`）を取得できない。60 秒リトライしても取れない | はい |
| 7 | 先行する別の `gz_setup` が終わらず、ロックを取れない（待つ上限は先行の最悪経路の見積もり（世界待ち + 状態取得 + マーカー 2 回 + カメラ + 物体 + 余裕。既定で約 40 分）。先行が異常ではなく遅いだけのこともある） | 先行のログを確認してから |
| 64 | コマンドラインの誤り（姿勢の要素数、`--world` の文字、`--wait-sec` の値など）。待っても直らない | 引数を直してから |

`ros2 run ros_gz_sim create` がタイムアウトや非ゼロで終わっても、Gazebo 側で後から完了することがある。そのため成否は「`create` の終了コード」ではなく「モデルが `scene/info` に現れたか」で判断する（現れなければ、目印は 1 回だけ再スポーンする）。Ctrl-C / SIGTERM では子プロセスを含めて止める。

## 隔離方針との関係

- 仮想カメラのトピックは `ros2arm` が参加する既存の経路（`default` と `ros2-lab-net`）で `ros2lab` に届く。compose の変更は不要。
- **DDS は無認証**。`ros2-lab-net` に `kali-vnc` などを接続すると、そこからカメラトピックを購読でき、偽の画像・深度・CameraInfo や `/tf`・`/tf_static` を注入できる。簡易検出スクリプトは入力を信頼して `target_0` を出す（公式の `pick_and_place_tf` は `target_0` を読む）ので、**注入されるとシミュ上のアームが攻撃者の指定した位置へ動きうる**。VLA の入力も同様に信頼してはならない。
- **Gazebo の通信（gz-transport）も無認証**。`gz_setup` が使う `entity/system/add` は、同じ Gazebo の通信網にいる誰でも呼べ、任意のライブラリをロードさせられる（ワールドの変更も同様）。`ros2-lab-net` を越えて gz-transport の Discovery が届くかは**未検証**（ros2lab には `gz` が無いので、`kali-vnc` などから `gz service -l` を実行して確かめる）。**`GZ_PARTITION` は名前空間の分離であって認証ではない**（知っていれば合わせられる）ので緩和にならない。届く場合の候補は、`ros2arm` 内の gz を `GZ_IP=127.0.0.1` で動かして外に出さないこと（未検証。別 PR で compose / イメージの設定として扱う）。この PR は compose を変えていないので従来のリスクが増えるわけではないが、このサービスを使い始める。
- 実カメラを接続するときは `ros2real` 側で動かし、画像を `ros2-lab-net` に出さない構成を維持する（`ros2real` は `lab` に繋がない）。

## トラブルシュート

| 症状 | 原因・対処 |
|---|---|
| トピックは見えるが画像が来ない・1Hz 未満 | 負荷。`gz topic -e -t /stats` で RTF を見る。adapter は購読後 10 秒来ないと警告を出す。`ros2 topic hz /sim_camera/<プロファイル名>/raw/image` で bridge の手前から切り分ける。センサは `always_on` なので、購読者がいなくても Gazebo は描画し続ける（lazy で省けるのは bridge と adapter の変換・転送だけ） |
| `gz_setup` が終了コード付きで止まる | 下の表を参照 |
| `compressed:=true`（既定）でも購読者がいないとき | `republish` が入力を遅延購読する（出力側に購読者がいるときだけ購読する）かどうかは**未確認**（Jazzy の `image_transport` の実装に依存し、色ストリームでの実測も未実施）。常時購読なら、色画像の変換は購読者がいなくても走る |
| `ros2lab` にトピックが出ない | Discovery に 10〜20 秒かかる。`ros2arm` で launch が動いているか、同じ `ROS_DOMAIN_ID`（42）かを確認 |
| `gz_setup` が `ワールド default のサービスが立たない` で終わる | 公式 launch を先に起動していない、または Fuel のモデルダウンロード待ち（外向き HTTP が必要）。待ってから `sim_camera.launch.py` を再実行 |
| 画像が灰色一色 | 古い版では `ros_gz_sim create` が SDF の姿勢を無視していた。最新の `ros2_poc_sim` を使う（姿勢を `-x -y -z -R -P -Y` で明示している） |
| 同じプロファイルを再実行したら二重に動いた | bridge / adapter は ROS ノードなので、動かしたまま同じプロファイルで再実行しない（先に Ctrl-C）。Gazebo 側（Sensors・モデル）は冪等 |
