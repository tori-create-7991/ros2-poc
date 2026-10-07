# シナリオ実行・判定・録画（run-scenario.sh）

Mac のターミナルから 1 コマンドで、Gazebo 上の CRANE-X7 に関節指令を順に送り、
仮想カメラの映像で「指令どおり動いたか」をステップごとに PASS / FAIL 判定し、
デスクトップ画面・カメラ映像・命令文・判定結果を 1 本の mp4 に残す。実機（ros2real）へ移る前の確認用。

```bash
bash scripts/up-arm.sh                                   # 未起動なら
bash scripts/run-scenario.sh --start-sim                 # シミュも起動して既定シナリオを実行
bash scripts/run-scenario.sh --scenario examples --repeat 2
bash scripts/run-scenario.sh --scenario <path/to/my.yaml>
```

| 引数 | 既定 | 内容 |
|---|---|---|
| `--scenario` | `default` | `default` / `fail_demo` / `examples`（`ros2_poc_sim/config/scenarios/`）または YAML のパス（相対パスは実行したディレクトリから） |
| `--repeat` | YAML の `repeat`（無ければ 1） | シナリオ全体の繰り返し回数（1〜20） |
| `--start-sim` | なし | シミュ（公式 Gazebo + MoveIt + 仮想カメラ、視点 `fixed_front_wide`）が動いていなければ起動する。カメラ付きのシミュが起動途中なら起動せずに待ち、カメラ無しのシミュ（README のデモ 2 など）が動いているときは重ねて起動せずに止める |
| `--timeout` | 120（`--start-sim` 時 900） | トピックが流れ始めるまで待つ秒数 |

終了コード: `0` = 全ステップ PASS / `1` = FAIL あり / `2` = 環境・記録の問題（判定できなかった。camera_info が届かない・
camera.mp4 を書き終えられない・記録プロセスを止められない・指令を送れなかった等を含む）/ `64` = 引数・シナリオの誤り /
`130` = Ctrl-C（記録とロックは後片付けする。送信中の指令はコンテナ内で最大 30 秒残りうる）。
指令を送れなかったステップがあるときも `result.json` は書く（該当ステップは `send_failed`）。
合成（scenario.mp4）に失敗しても終了コードは判定結果のまま（判定の正は `result.json`）。

実行前に次を確かめ、満たさなければ `2` で止まる。

- `ros2real`（実機ドライバ）が起動していない
- ros2lab-a が SROS2 環境 a で起動している（ラベル `ros2poc.env`。ラベル無しは a 扱い）。ros2arm も SROS2 環境 a であること（環境 b / c の ros2lab-a はアームのトピックに触れず、ros2arm と環境が違うと DDS で通信できない）。b / c なら `bash scripts/up-env.sh a --arm` で戻す
- ros2arm に ffmpeg（libx264・drawtext 等）・xdpyinfo・フォントがある（`scenario_cli doctor`）
- 別の run-scenario が実行中でない（ros2arm 内の `/tmp/run-scenario.lock`）、前回の記録プロセスが残っていない
- ros2lab-a からアームのコントローラ（`crane_x7_arm_controller`）がちょうど 1 つ見える（Discovery 待ち。2 つ以上ならシミュの二重起動か実機と混在として止める）
- 送るコマンドが想定の形（送信先ごとにトピック・型まで固定し、メッセージは二重引用符の中の数値・名前・記号のみ）

## 出力（`workspace/runs/<日時>/`）

`workspace` は ros2arm の `/workspace` のマウント元（`up-arm.sh` を実行した checkout の `./workspace`）。

| ファイル | 内容 |
|---|---|
| `scenario.mp4` | 合成動画。左: デスクトップ（x11grab）、右: 仮想カメラ（判定後は変化領域の枠と手先の投影点）、下帯: 命令と判定 |
| `result.json` | ステップごとの判定・根拠の数値（関節誤差、変化画素率、手先の投影点、理由）。random の展開値は `expect`。`performance` に RTF・所要時間（下の「性能の測定」） |
| `clock.csv` | `wall,sim`（約 1Hz）。`/clock` の sim time と壁時計。RTF の元データ |
| `run_meta.json` | `RUN_LABEL`・Colima VM の CPU 数とメモリ（GiB）・シナリオ名 |
| `commands.log` | 実際に送ったコマンドの全文と送信先 |
| `camera.mp4` / `desktop.mp4` | 記録の元データ |
| `camera_frames.csv` / `joints.jsonl` / `ee.jsonl` / `camera_info.json` / `events.jsonl` / `steps.json` | 判定の入力（`scenario_cli judge` で判定し直せる） |

1 回あたり 3〜5MB（デスクトップ録画が大半。既定シナリオの実測）。`workspace/runs/` は自動では消えないので、
不要になったら `workspace/runs/<日時>` を消す（ros2arm が作ったファイルなので、ホストで消せないときは
`docker exec -u ubuntu ros2arm rm -rf /workspace/runs/<日時>`）。

判定や合成だけをやり直す:

```bash
docker exec -u ubuntu ros2arm bash -c 'source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash; source /opt/ros2_poc_ws/install/setup.bash; ros2 run ros2_poc_sim scenario_cli judge /workspace/runs/<日時> && ros2 run ros2_poc_sim scenario_cli compose /workspace/runs/<日時>'
```

## 構成

```
Mac: scripts/run-scenario.sh
  ├─ ros2lab-a: ros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory ...（README と同じ経路。--once は Discovery 直後に落ちることがあるので 3 回送る）
  ├─ ros2arm:   ros2 action send_goal /crane_x7_gripper_controller/gripper_cmd ...（ros2lab に control_msgs が無いため）
  ├─ ros2arm:   scenario_observer（カメラ・/joint_states・手先 TF を記録）、ffmpeg x11grab（デスクトップ）
  └─ ros2arm:   scenario_cli wait / judge / compose
```

記録してから判定する（録画中は判定しない）。CPU 描画で重い環境でも記録が詰まらず、判定は記録データから決定論的にやり直せる。
送信前後の時刻はコンテナ内の `date` で取り、記録ノードの `time.time()` と同じ Colima VM の時計で揃える。

## シナリオの書き方

```yaml
description: 任意
repeat: 1                      # 全体の繰り返し（--repeat で上書き）。名前に #1, #2 が付く
steps:
  - name: pose_a               # 英数字・_・-（40 文字以内）
    positions: [0.5, 0.3, 0.0, -1.2, 0.0, -0.5, 0.0]   # 7 関節 [rad]。リミット外は実行前にエラー
    time_from_start: 3         # 既定 3
    tolerance: 0.05            # 任意（既定 0.05 rad）
    expect: [...]              # 任意。判定の期待値だけ差し替える（fail_demo 用）
  - name: wave                 # 経由点。判定は最後の点
    waypoints:
      - {positions: [...], time_from_start: 2}
      - {positions: [...], time_from_start: 4}   # 単調増加
  - name: grip
    gripper: close             # open（60°）/ close（0）/ 角度 [rad]。許容は 0.1 rad
  - name: random_pose          # 関節リミットの中央 50% の範囲でランダム。seed で再現
    random: {n: 3, seed: 1, time_from_start: 3}
```

関節の順は `shoulder_fixed_part_pan`, `shoulder_revolute_part_tilt`, `upper_arm_revolute_part_twist`,
`upper_arm_revolute_part_rotate`, `lower_arm_fixed_part`, `lower_arm_revolute_part`, `wrist`（すべて `crane_x7_` 始まり）。

## 判定（各ステップ、全部満たせば PASS）

| # | 条件 | 既定 |
|---|---|---|
| 1 | 関節: 判定時刻の `/joint_states` と期待値の差が全関節で許容内 | アーム 0.05 rad / グリッパ 0.1 rad |
| 2 | 映像で動いた: 送信前と判定時刻のカメラフレームの変化画素率 | アーム ≥ 0.5% / グリッパ ≥ 0.1%（差 > 25/255 を変化とみなす） |
| 3 | 映像と姿勢の一致: 手先（`crane_x7_gripper_base_link`）の TF を camera_info で投影した点が、変化領域の外接矩形（余白 40px）の中 | |
| 4 | 静止: 判定時刻以後の最初のフレームと、その 1.0 秒以上あとのフレームの変化画素率 | ≤ 0.2% |

- フレームは「送信開始以前の最後の 1 枚」「判定時刻以後の最初の 1 枚」「その 1.0 秒以上あとの 1 枚」を使う。ホストが重いとカメラは 1fps 以下（間隔 7 秒）まで落ちるので、間隔は 10 秒まで許容する（実測）。
- 期待姿勢が送信前と同じ（動かない指令）なら、2 は「映像が変化しないこと」に反転し、3 は見ない。
- **判定時刻は指令時間ではなく `/joint_states` の静止で決める**（実行中の待ち `scenario_cli wait` と事後の判定が同じ規則を使う）。CPU 描画ではシミュの実時間比（RTF）が 0.2〜0.4 まで落ち、
  3 秒の指令が実際には 7〜15 秒かかる（実測）。静止（0.8 秒間の変化 ≤ 0.002 rad）を検出してから、
  カメラ映像の遅れ（関節より 1〜1.5 秒遅れる。実測）ぶん 1.5 秒待って判定する。
  `指令時間 × 5 + 10` 秒たっても止まらなければ、その時刻で判定し `joints_not_still` で FAIL にする。
- 動画の下帯の理由は ASCII の短いコード（`joint_err`、`no_motion`、`ee_outside_change`、`not_settled` など）。
  日本語の理由は `result.json` と端末に出る（ffmpeg 6.1 の drawtext はマルチバイト文字を含む行を途中で切るため）。

## 視点 fixed_front_wide

腕全体（真上に伸ばした home 姿勢の手先まで）と机上の物体が画角に入る三人称視点。
既存の `fixed_front_oblique` は机に近く低いため、手先が縦の画角（±27°）の外に出て条件 3 が成り立たない。
すでに別の視点でシミュが動いている場合は、そのまま使われる（`--start-sim` は動いていないときだけ起動する）。
視点を変えるときは、シミュを止めてから `--start-sim` で起動し直す。`--start-sim` で起動したシミュは実行後も動き続け、
ログは `workspace/runs/sim-<日時>.log` に出る。止めるには:

```bash
docker exec ros2arm pkill -INT -f '[r]os2 launch ros2_poc_sim'   # 数秒で Gazebo / MoveIt / RViz ごと止まる
```

## トラブルシュート

| 症状 | 原因・対処 |
|---|---|
| `トピックが流れない` で終わる | シミュが起動していない。`--start-sim` を付ける。初回は Gazebo の起動に数分〜十数分かかる |
| `カメラのフレームが記録されない` | 仮想カメラが出ていない。`workspace/runs/<日時>/observer.log` と `ros2 topic hz /camera/color/image_raw` を確認 |
| 全ステップ `ee_outside_change` / `no_ee_projection` | 手先が画角外の視点で動いている。`fixed_front_wide` で起動し直す |
| `joints_not_still` | シミュが極端に遅い、またはコントローラが目標に届かない。`gz topic -e -t /stats` で RTF を確認 |
| 動画の左側が RViz で、Gazebo の GUI が見えない | デスクトップをそのまま録画しているため。Gazebo のシーンは右側の仮想カメラで見える。必要なら noVNC で Gazebo のウィンドウを前に出してから実行する |
| `合成に失敗した` | `overlay/filtergraph.txt` と ffmpeg のメッセージを確認 |
| `別の run-scenario が実行中` | 記録プロセスが動いている。終わるのを待つ |
| `前回の run-scenario のロックが残っている` | 前回を強制終了した（SIGKILL・VM 停止など）。記録プロセスが無いことを確かめて `docker exec ros2arm rm -r /tmp/run-scenario.lock`。ロックはコンテナを作り直すと消えるが、`docker restart` や Colima の再起動では残る |
| `前回の記録プロセスが残っている` | 前回を強制終了した。案内のコマンドで止めてから再実行する。記録プロセスは停止の指示が届かなくても、シナリオから計算した上限時間で止まる |
| `記録プロセスを止められない` | 判定に使う camera.mp4 が未完の可能性があるので判定しない（終了コード 2） |

## 性能の測定（RTF・所要時間）

シミュが実時間より遅いと、3 秒の指令が 9〜13 秒かかり、カメラのフレームも減る。設定を変えたときの効果は数値で比べる。

```bash
RUN_LABEL=default bash scripts/run-scenario.sh      # ラベルは英数字・_・- の 32 文字まで
RUN_LABEL=cpu6    bash scripts/run-scenario.sh      # Colima の CPU を変えたあと、など
docker exec -u ubuntu ros2arm bash -lc 'source /opt/ros/jazzy/setup.bash; source /opt/ros2_poc_ws/install/setup.bash; \
  ros2 run ros2_poc_sim scenario_cli perf /workspace/runs/<日時1> /workspace/runs/<日時2>'
```

| 列 | 意味 |
|---|---|
| RTF | Δsim / Δwall。1.0 で実時間どおり、小さいほど遅い。区間は下の 3 列と同じ（最初のフレーム〜最後のステップの判定時刻）。区間が 1 秒未満・`clock.csv` が無い run は `-` |
| RTF最小 | `clock.csv` の 5 行（1Hz なら約 5 秒）ごとの RTF の最小（一時的な落ち込み） |
| wall[s] | 最初のフレームから最後のステップの判定時刻までの壁時計 |
| cam[fps] | 同じ区間でのカメラフレームの平均記録レート（上限 30fps で間引く） |

`result.json` の `performance.steps` にステップごとの `wall_sec` と `sim_sec` も出る（指令の長さに対して実際にかかった時間）。`clock.csv` は約 1Hz なので、記録の末尾に終わったステップの `sim_sec` は `null` になることがある。
`/clock` が記録されない（`clock.csv` がヘッダだけ）と RTF は全て `-` になり、判定時に端末へ理由が出る（PASS / FAIL には影響しない）。比べる表にシナリオ・メモリの列があり、シナリオが混在すると注意が出る。
比べるときは、同じシナリオ・同じ視点で、他の重い処理（別セッションの推論コンテナなど）を止めてから測る。

## 制約

- シミュ専用。実機（ros2real）では使わない（スクリプトが拒否する）。
- グリッパの命令は ros2arm から送る。実機でグリッパを動かすには、ros2lab に `control_msgs` を入れるか ros2real 側から送る必要がある（未対応）。
- DDS は無認証（[sim-camera-profile.md](sim-camera-profile.md) の「隔離方針との関係」と同じ）。同じドメインの誰でも画像・CameraInfo・TF・`/joint_states` を偽装でき、判定（PASS / FAIL）を変えられる。**判定が意味を持つのは信頼できるネットワーク内だけ**。`ros2-lab-net` に信頼できないコンテナを繋いだまま実行しない。記録ノードは画像の大きさ（1920×1080 まで）・頻度（30fps まで）・CameraInfo の値を検査し、記録時間にも上限を置いている。
- 録画・合成の ffmpeg・xdpyinfo・フォントはベースイメージ由来（Dockerfile では宣言していない）。ベースイメージを更新して無くなった場合は `scenario_cli doctor` で実行前に止まる。
