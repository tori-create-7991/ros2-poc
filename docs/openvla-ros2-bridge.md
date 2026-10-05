# OpenVLA × ROS 2 連携（ros2server + vla-server）

仮想カメラの画像と指示文から、VLA（Vision-Language-Action）モデルの手先差分でシミュ上の CRANE-X7 を動かす入口。
推論は REST サーバー（OpenVLA の `deploy.py` の `POST /act`）に分け、GPU の無い Mac + Colima と CI では **GPU 不要のスタブ（`vla-server`）** で回す。
本物の OpenVLA サーバー（別マシンの GPU）とは URL を差し替えるだけで入れ替わる。

- **完了の範囲**: スタブでのパイプライン疎通まで。**OpenVLA が CRANE-X7 を正しく動かすかは未検証**（下の「未検証」）。
- arm 層（`ros2arm` → 将来 `ros2real`）には手を入れない。標準の ROS 2 インターフェース（TF・`/compute_ik`・`joint_trajectory`・`gripper_cmd`・`/joint_states`）だけを DDS 越しに使う。
- 調査・設計の背景は `.research/openvla-ros2_20261002/`（git 管理外。数値は要約経由なので採用前に原文で再確認する）。

## 構成

```
 default ネットワーク（ROS_DOMAIN_ID=42）                         lab ネットワーク（ros2-lab-net, internal）
 ┌───────────────────────────┐ HTTP POST /act ┌───────────────┐
 │ ros2server                 │ ─────────────▶ │ vla-server    │   ros2server / ros2arm / ros2lab-a/b / kali-vnc / ros2diag
 │ vla_node + vla_converter   │ ◀───────────── │（スタブ）      │   が同じ lab に参加（vla-server は default のみ）
 └────────────┬──────────────┘                └───────────────┘
              │ DDS（標準インターフェースのみ）
              ▼
 ┌────────────────────────────────────────────────┐
 │ ros2arm（Gazebo + MoveIt + 仮想カメラ。無改造）   │
 └────────────────────────────────────────────────┘
```

| コンテナ | 役割 | ネットワーク |
|---|---|---|
| `ros2server` | VLA ノード（画像 → `/act` → `/vla/action`）と変換ノード（手先差分 → IK → アーム指令）。`ros2lab` と同じ使い勝手（ROS CLI、`./workspace`） | `default` + `lab` |
| `vla-server` | `POST /act` のスタブ。標準ライブラリだけの Python。読み取り専用・権限なし。ホストへは `127.0.0.1:8000` のみ | `default` のみ |
| `ros2arm`（既存） | シミュ。`/compute_ik`（MoveIt）、`gripper_cmd`、`joint_trajectory`、TF、`/joint_states`、仮想カメラ | `default` + `lab` |

1 ステップ: (1) 仮想カメラ画像を取る → (2) 256x256 にして `/act` へ → (3) 7 次元の差分を受ける → (4) `/vla/action` に出す → (5) 変換ノードが TF の現在姿勢に差分を足して `/compute_ik` で関節角にし、`joint_trajectory`（グリッパは `gripper_cmd`）で送る → (6) `/joint_states` が目標で静止したら `/vla/ack` を返す → 次のステップ。

実時間周期（OpenVLA 論文の 5 Hz）ではなく **1 ステップずつ完了を待つ**。CPU 描画のシミュは RTF が 0.2〜0.4 まで落ち、周期に届かないため。

## 使い方

```bash
# 1. ros2arm を起動し、カメラ付きのシミュを立てる
bash scripts/up-arm.sh
#    noVNC（http://127.0.0.1:6080）のデスクトップの端末で（詳しくは sim-camera-profile.md）:
#    ros2 launch ros2_poc_sim arm_with_camera.launch.py placement:=fixed_front_wide
#    Gazebo の起動には初回で数分〜十数分かかる

# 2. ros2server と vla-server を起動する（既存の ros2lab-a/b などは作り直さない）
docker compose --profile vla up -d --build ros2server vla-server

# 3. 3 ステップ、手先を上へ
bash scripts/run-vla.sh --instruction "move up" --steps 3
```

| 引数 | 既定 | 内容 |
|---|---|---|
| `--instruction` | （必須） | 指示文（英数字・空白・`.,_!?-` のみ、200 文字まで）。スタブのキーワードは下の表 |
| `--steps` | 3 | ステップ数（1〜100） |
| `--endpoint` | 同じ compose の `vla-server` | `POST /act` の URL。本物の OpenVLA サーバーに替えるときに指定 |
| `--unnorm-key` | なし | OpenVLA の `unnorm_key`。スタブは無視する。**CRANE-X7 向けは未検証** |
| `--timeout` | 120 | アームのコントローラが見えるまで待つ秒数 |

終了コード: `0` = 全ステップ ok / `1` = ok でないステップがあった（`ik_failed` / `rejected` / `timeout`。最初の失敗で止まる） / `2` = 環境の問題 / `64` = 引数の誤り / `130` = Ctrl-C。

実行前に次を確かめ、満たさなければ `2` で止まる。

- `ros2real`（実機ドライバ）が起動していない
- `ros2arm` / `ros2server`（`--endpoint` 未指定なら `vla-server` も）が起動している
- `ros2server` が SROS2 環境 a（`ros2arm` は SROS2 化しておらず環境 a でしか通信できない）
- `run-scenario.sh` が実行中でない（`ros2arm` の `/tmp/run-scenario.lock`。同じアームに指令が混ざるため）、別の `run-vla.sh` が実行中でない
- `ros2server` からアームのコントローラ（`crane_x7_arm_controller`）がちょうど 1 つ見える（Discovery 待ち。2 つ以上ならシミュの二重起動か実機との混在として止める）

`ros2server` は `docker compose exec ros2server bash` で入って、ROS CLI や `ros2 run ros2_poc_sim vla_node ...` を手で使える。

## スタブ（`vla-server`）のキーワード

指示文を小文字にして単語として照合し、複数あれば足し合わせる（決定論的）。`base_link` 基準（x 前、y 左、z 上）。1 ステップの並進は 0.02 m。

| 単語 | 差分 | 単語 | 差分 |
|---|---|---|---|
| `up` | z +0.02 | `down` | z -0.02 |
| `left` | y +0.02 | `right` | y -0.02 |
| `forward` | x +0.02 | `back` / `backward` | x -0.02 |
| `close` | gripper 0（閉） | `open`（既定） | gripper 1（開） |

どれにも当たらない指示は、動かず gripper 1 を返す。画像は形（H, W, 3 の uint8）だけ検査し、中身は見ない。

## 契約

### `POST /act`（`ros2server` ⇄ VLA サーバー）

OpenVLA の `deploy.py` の原文（2026-10-05 確認）に合わせている。

- リクエスト: `{"image": <ndarray>, "instruction": str, "unnorm_key": 省略可}`。ndarray は json_numpy 形式 `{"__numpy__": <base64>, "dtype": "|u1", "shape": [256, 256, 3]}`（json-numpy **2.1.1** のソースで確認。他の版で形式が違う可能性があり、本物のサーバーとの結合は未検証）。`{"encoded": <JSON 文字列>}` の二重エンコードも受ける。
- レスポンス: ndarray（json_numpy 形式）**そのもの**。`deploy.py` の docstring は `{"action": ndarray}` と書くが、実装は `JSONResponse(action)` で配列を直接返す。クライアントは両方を受ける。
- **エラー時も HTTP 200 で文字列 `"error"` を返す**。クライアントは型と 7 次元・有限値を検査して弾く（`vla_client.parse_response`）。
- サーバーの既定の待ち受けは `0.0.0.0:8000`（認証なし）。

### `/vla/action` と `/vla/ack`（`vla_node` ⇄ `vla_converter`、DDS）

`ros2server` にカスタムメッセージは作れないので、標準の `std_msgs/String` に JSON を載せる。他のノードからも同じ契約で動かせる。

- `/vla/action`: `{"seq": int>=0, "delta": [dx, dy, dz, droll, dpitch, dyaw], "gripper": 0..1}`。`delta` は `base_link` 基準、並進 [m]・回転 [rad]（ベース基準の増分）。gripper は 0（閉）〜1（開）。
- `/vla/ack`: `{"seq": int, "status": "ok|rejected|ik_failed|timeout", "reason": str}`。

変換ノードは同時に 1 ステップだけ処理する（処理中の指令は `rejected`「busy」）。不正な指令でも `seq` が読めれば `rejected` の ack を返す（読めなければ ack を返せず、VLA ノードは待ち続けて終了コード 2 になる）。VLA ノードの `seq` は実行ごとにランダムな値から始め、前回の遅い ack と取り違えない。1 ステップの最悪待ちは約 90 秒（TF・`/joint_states` 各 15、IK 8、購読者待ち 10、グリッパ 25、静止待ち約 19）で、VLA ノードの `--ack-timeout`（既定 240 秒）より短い。

## 安全（入力を信頼しない）

DDS は無認証（環境 A）。`/vla/action` には誰でも書けるので、変換ノードは次を強制する。

| 項目 | 内容 |
|---|---|
| 検証 | JSON の形、`seq` は 0 以上の整数、`delta` は 6 個の有限な数値（絶対値 1000 以下）、`gripper` は有限。不正は破棄 |
| 1 ステップの上限 | 並進のノルム 0.03 m、回転のノルム 0.1 rad（向きは保ってクランプ。`reason` に記録） |
| 作業空間 | `base_link` 基準で x, y ∈ [-0.5, 0.5] m、z ∈ [0.05, 0.7] m の箱に収める |
| IK の解 | 関節数・有限値・関節リミットを検査。現在から 0.8 rad を超えて飛ぶ解は拒否（IK が別の解へ反転した可能性） |
| 同時実行 | 1 ステップのみ。`run-vla.sh` のロックで同時実行も拒否 |
| VLA の応答 | 7 次元・有限値・サイズ上限 1MB。`"error"` は拒否。リダイレクトには追従しない（検証した URL 以外へ画像を送らない） |

`lab` ネットワーク（`ros2-lab-net`）に `kali-vnc` などを繋ぐと、そこから `/vla/action` や `joint_trajectory` を偽装でき、シミュ上のアームを動かせる（`ros2arm` の既存のリスクと同じ。[sim-camera-profile.md](sim-camera-profile.md) の「隔離方針との関係」）。クランプは被害を小さくするだけで、認証の代わりにはならない。受理レートの上限や `/vla/ack` の偽装対策は無い（連打すれば作業空間の端まで動かせる）。`--endpoint` は平文 HTTP なので、信頼できる閉じたネットワークの GPU サーバー宛だけに使う（画像が平文で流れる）。**判定や動作が意味を持つのは信頼できるネットワーク内だけ**。

## 本物の OpenVLA サーバーを使う（手順のみ。この環境では検証していない）

GPU（VRAM は bf16 で約 17GB、int8 で約 10GB、int4 で約 7GB 級。論文の値で、要約経由なので採用前に原文で確認する）のあるマシンで、OpenVLA の `deploy.py` を動かす。依存は `deploy.py` の docstring どおり（サーバー側 `uvicorn` / `fastapi` / `json-numpy` に加え OpenVLA の依存）。

```bash
# GPU マシンで（<openvla-checkout> は OpenVLA のリポジトリ）
cd <openvla-checkout>
python vla-scripts/deploy.py --host 127.0.0.1 --port 8000   # 既定は 0.0.0.0（認証なし）なので、外へ出さない
```

- **`--host 127.0.0.1` を付ける**。`deploy.py` は認証が無く、`0.0.0.0` のままだと同じネットワークの誰でも推論を呼べる。他のマシンから使うなら SSH のポートフォワード（`ssh -L 8000:localhost:8000 <user>@<gpu-host>`）か、信頼できる閉じたネットワークだけに限る。
- Mac + Colima の `ros2server` コンテナから、ホスト側のポートフォワードに届くかは**未検証**（コンテナからホストのループバックは見えない）。GPU マシンへ直接届く閉じたネットワークなら `--endpoint http://<gpu-host>:8000/act` で指す。
- 重みは Hugging Face `openvla/openvla-7b`（コードは MIT、モデルは Llama Community License）。

```bash
bash scripts/run-vla.sh --instruction "pick up the blue cube" --steps 5 \
  --endpoint http://<gpu-host>:8000/act --unnorm-key <データセットの名前>
```

## 未検証（本物の OpenVLA で使う前に確認すること）

| 項目 | 状況 |
|---|---|
| CRANE-X7 が OpenVLA の学習データ（Open X-Embodiment）に含まれるか | **未確認**（WidowX、Google Robot、Franka など 15 種類以上。CRANE-X7 は確認できていない）。含まれなければ、`unnorm_key` の統計もロボットの動きも合わない |
| `unnorm_key` | 候補（例: WidowX 向けの `bridge_orig`）は**推測**。CRANE-X7 に妥当な値は無い可能性が高く、微調整が要る（LoRA で VRAM 約 27〜72GB 級、デモ 10〜150 件。論文・README の値で要確認） |
| 差分の座標系 | 並進・回転とも `base_link` 基準の増分として扱っている（`vla_action.apply_delta`）。OpenVLA の差分が手先座標系かベース座標系かはロボットごとに違い、未検証 |
| グリッパ値の意味 | 0.5 を閾値に、高いほど開くとして扱っている（変換ノードのパラメータ `gripper_open_when_high` で向きを反転できる）。学習データでの向きは未検証 |
| 実時間の制御（5 Hz） | 対象外。シミュの RTF では届かない。GPU 実機への移行時に non-blocking 設計が要る |
| 実機（`ros2real`） | 未検証。arm 層が同じインターフェース（`/compute_ik`、`gripper_cmd`、`joint_trajectory`、TF、`/joint_states`）を出せば変換ノードはそのまま使える想定 |
| SROS2 環境 b / c | 未対応（`ros2arm` が SROS2 化されていない）。`run-vla.sh` は `ros2server` が環境 b / c なら起動前に止める |

## 検証結果（2026-10-05、Colima 4 CPU、シミュ起動中）

`ros2arm`（既存）+ `ros2server` + `vla-server`（スタブ）で `run-vla.sh` を実行した。手先は `tf2_echo base_link crane_x7_gripper_base_link` の z。

| 実行 | 結果 |
|---|---|
| `--instruction "move down" --steps 3` | 全ステップ ok、終了コード 0、約 33 秒。z が 0.624 → 0.564 m（1 ステップ 0.02 m ちょうど） |
| `--instruction "move up" --steps 2` | 全ステップ ok、終了コード 0。z が 0.564 → 0.604 m |
| `--instruction "close the gripper" --steps 1` | ok（グリッパは変化したときだけ送る。最初のステップは必ず送る） |
| 大きすぎる差分（x 0.5 m） | 0.03 m にクランプされ、`reason` に記録される（変換ノード単体の実行で確認） |
| `vla-server` 停止中 | 終了コード 2（起動していない旨） |
| 接続できない `--endpoint` | 終了コード 2（`サーバーに繋がらない`） |

ROS 非依存の単体テストは `cd ros2_poc_sim && python -m pytest -q test/test_vla_*.py`（CI の pytest ジョブで実行）。ガードの分岐は `bash scripts/test-run-vla.sh`（docker スタブ）。

## 既知の挙動・制約

- **初期姿勢は到達範囲の端**。腕が真上に伸びきった姿勢（手先 z ≈ 0.624 m）は特異点で、そこへ戻す差分は IK の解が出ず `ik_failed` になる（z = 0.604 から +0.02 で確認）。フェイルセーフとして期待どおり。初期姿勢に戻すときは関節角の指令を使う。
- **最初のステップは遅い**。変換ノードを起動した直後は `/tf` と `/joint_states` の Discovery が終わっておらず、最大 15 秒待つ。
- **カメラ映像は `/joint_states` より約 1〜1.5 秒遅れる**ので、2 ステップ目以降は 1.5 秒待ってから新しいフレームを取る（[sim-scenario-recording.md](sim-scenario-recording.md) と同じ実測）。
- 変換ノードは前のステップの `gripper` を覚えていて、変化したときだけ `gripper_cmd` を送る。起動直後の最初のステップは必ず送る（現在のグリッパ状態を読まないため）。
- シミュ（CPU 描画）は遅く、`ros2arm` のシナリオ実行（`run-scenario.sh`）と同時には動かせない。排他は**片方向**で、`run-vla.sh` は `run-scenario.sh` のロックを見るが、`run-scenario.sh` は `run-vla` のロックを見ない（確認してから実行までの隙間も残る）。同時に実行しない。
- アームの指令を送ったあとにグリッパが失敗すると、アームは動いたのに `rejected`（reason に「アームの指令は送信済み」）になる。
- `run-vla.sh` の Ctrl-C / SIGTERM では VLA ノードと変換ノードを止めてロックを外す（`docker exec` はシグナルを転送しないため）。この後始末は docker スタブのテストの範囲で、実コンテナでの確認は未実施。
- `ros2server` は `ros2lab` と同じ使い勝手のため root・権限制限なしで動く（`vla-server` は読み取り専用・権限なし）。
- ROS 依存部（`vla_converter.py` / `vla_node.py`）に単体テストは無く、実コンテナでの検証のみ（CI は構文チェックだけ）。手順は ROS 非依存の `vla_step` / `vla_loop` に寄せてテストしている。
- 実機（`ros2real`）へ移す前に: 作業空間・上限・関節の変化量をパラメータ化し、arm 定数を `scenario`（シミュ用）から切り出す。変換ノードを実機で単独起動できないようにするガードも要る（現状の実機拒否は `run-vla.sh` のみ）。

## トラブルシュート

| 症状 | 原因・対処 |
|---|---|
| `ros2server から crane_x7_arm_controller が見えない` | シミュが起動していない、または Discovery の遅れ。数十秒待つ。`docker exec ros2server bash -lc 'ros2 topic list'` で `/camera/color/image_raw` が見えるか確認 |
| `run-scenario が実行中` | シナリオ実行が同じアームを使っている。終わってから実行する。強制終了の残りなら `docker exec ros2arm rm -r /tmp/run-scenario.lock`（実行中でないと確かめてから） |
| 終了コード 2 で `ack が来ない` | 変換ノードが落ちた。`docker exec ros2server tail -n 20 /tmp/vla_converter.log` |
| `rejected`（busy） | 前のステップを処理中。`run-vla.sh` は 1 ステップずつ待つので通常は出ない。手で `/vla/action` を連続で出すと出る |
| `ik_failed` | 目標が到達範囲の外（初期姿勢の端など）。差分を小さくする・向きを変える |
| `rejected`（関節の変化が大きすぎる） | IK が別の解へ飛んだ。差分を小さくする |
| `カメラ画像が来ない` | 仮想カメラが出ていない。`docs/sim-camera-profile.md` のトラブルシュート |
