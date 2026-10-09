# シナリオ実行の流れ（default シナリオ）

`bash scripts/run-scenario.sh` が 1 回の実行で何をするかを、シーケンス図とフロー図で示す。
使い方・判定条件の詳細は [sim-scenario-recording.md](sim-scenario-recording.md) を参照。

## シーケンス図

```mermaid
sequenceDiagram
    autonumber
    participant U as Mac (run-scenario.sh)
    participant L as ros2lab-a
    participant A as ros2arm (Gazebo / 仮想カメラ)
    participant R as 記録 (observer / ffmpeg)

    U->>U: 事前チェック (実機が止まっている / SROS2 環境 a / コントローラ 1 つ)
    U->>A: 記録開始
    A-->>R: カメラ・joint_states・手先 TF・デスクトップ録画

    loop 5 ステップ (home, pose_a, grip_close, grip_open, home_again)
        alt アーム指令 (home / pose_a / home_again)
            U->>L: ros2 topic pub joint_trajectory
            L->>A: 関節指令 (DDS)
        else グリッパ指令 (grip_close / grip_open)
            U->>A: ros2 action send_goal gripper_cmd
        end
        A-->>R: アームが動く様子を記録
        U->>A: scenario_cli wait (joint_states が静止するまで待つ)
    end

    U->>R: 記録を停止 (camera.mp4 を確定)
    U->>A: scenario_cli judge (記録から判定)
    Note over A: 各ステップ 4 条件で PASS / FAIL<br/>1 関節誤差 2 映像変化 3 手先位置 4 静止
    A-->>U: result.json (全 PASS なら終了コード 0)
    U->>A: scenario_cli compose (scenario.mp4 を合成)
```

## フロー図

```mermaid
flowchart TD
    S([run-scenario.sh]) --> C{事前チェック}
    C -- NG --> E2[終了コード 2<br/>環境・記録の問題]
    C -- OK --> SIM{シミュ起動済み?}
    SIM -- 未起動かつ --start-sim --> START[Gazebo + MoveIt + 仮想カメラを起動]
    SIM -- 起動済み --> REC
    START --> REC[記録開始<br/>カメラ / joint_states / 手先 TF / デスクトップ]
    REC --> STEP[次のステップを取り出す]
    STEP --> KIND{種類}
    KIND -- アーム --> LAB[ros2lab-a から<br/>ros2 topic pub]
    KIND -- グリッパ --> GRIP[ros2arm から<br/>ros2 action send_goal]
    LAB --> WAIT[joint_states が静止するまで待つ]
    GRIP --> WAIT
    WAIT --> MORE{残りのステップ?}
    MORE -- あり --> STEP
    MORE -- なし --> STOP[記録停止]
    STOP --> JUDGE[judge: 各ステップを判定]
    JUDGE --> COMPOSE[compose: scenario.mp4 を合成]
    COMPOSE --> V{全ステップ PASS?}
    V -- はい --> OK([終了コード 0])
    V -- いいえ --> NG([終了コード 1])
```

## 1 ステップの判定（すべて満たして PASS）

```mermaid
flowchart LR
    J1[1. 関節<br/>期待値との差が許容内] --> J2[2. 映像が動いた<br/>送信前後で画素が変化]
    J2 --> J3[3. 映像と姿勢が一致<br/>手先の投影点が変化領域の中]
    J3 --> J4[4. 静止<br/>その後の映像が変化しない]
    J4 --> P([PASS])
```

## 出力

`workspace/runs/<日時>/` に `scenario.mp4`（左: デスクトップ、右: 仮想カメラ、下帯: 命令と判定）と
`result.json`（ステップごとの判定と数値）などが出る。
