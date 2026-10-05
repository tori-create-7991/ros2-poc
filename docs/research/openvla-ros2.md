# OpenVLA と ROS 2 連携の調査

OpenVLA でアームを動かす構成と、ROS 2 に載せる場合の論点をまとめる。
アームシミュ構成は [../arm-sim-architecture.md](../arm-sim-architecture.md)、GPU の扱いは
[../display-and-gpu.md](../display-and-gpu.md) を参照。

> 調査日: 2026-10-02。数値と記述は一次情報の要約を経由して取得したもので、採用前に原文での再確認が要る。
> 「未確認」と書いた項目は取得できなかったことを示す。

## 1. 結論

- OpenVLA は ROS 2 に対応していない。出力は手先の差分の数値配列で、ROS 2 との間には自作のノードが要る。
- 論文の構成は、GPU サーバーに VLA を置き、ロボット側 PC が画像を送って差分を受ける形。
  公式の `deploy.py` は REST（`POST /act`）で、これを ROS 2 ノードに置き換えるのが素直。
- 差分から関節角への変換、可動域・衝突の制限は、ロボット側の既存コントローラが担う。
  このリポジトリでは MoveIt Servo か CRANE-X7 公式ドライバがその層に当たる。
- 速度は GPU で大きく変わる。RTX 4090 で約 6 Hz、RTX 2060 で約 0.2 Hz。5 Hz 制御には 4090 級が要る。
- OpenVLA を ROS 2 から動かした公開例はある（シミュレーション 1 件、実機 1 件）。CRANE-X7 の例は見つからなかった。

## 2. 公式情報源

| 種別 | URL |
|---|---|
| 論文 | <https://arxiv.org/abs/2406.09246> |
| プロジェクトページ | <https://openvla.github.io/> |
| コード | <https://github.com/openvla/openvla> |
| 重み | <https://huggingface.co/openvla/openvla-7b> |
| REST サーバー | <https://raw.githubusercontent.com/openvla/openvla/main/vla-scripts/deploy.py> |

## 3. モデル仕様

| 項目 | 内容 |
|---|---|
| モデルサイズ | 7B（Llama 2 + SigLIP/DINOv2 融合エンコーダ） |
| 学習データ | Open X-Embodiment の 97 万軌道、A100 64 台で 15 日 |
| 入力 | カメラ画像 1 枚 + 言語指示 |
| 出力 | 7 次元の手先差分（x, y, z, roll, pitch, yaw, gripper） |
| 後処理 | 正規化済みで返るため、ロボット固有統計で逆正規化（`unnorm_key`） |
| 離散化 | 各次元を 256 bin |
| 精度 | RT-2-X（55B）を 29 タスクで +16.5pt、Diffusion Policy を +20.4pt |
| ライセンス | コード MIT、モデルは Llama Community License |
| 学習データ内のロボット | WidowX、Google Robot、Franka など 15 種類以上。CRANE-X7 は未確認 |

## 4. GPU 要件と速度

| 項目 | 値 | 出典 |
|---|---|---|
| 推論 VRAM（bf16 / int8 / int4） | 16.8 GB / 10.2 GB / 7.0 GB | 論文 |
| 推論速度 RTX 4090（bf16） | 約 6 Hz | 論文 |
| 推論速度 A5000 | int8 で 1.2 Hz、int4 で 3 Hz | 論文 |
| 推論速度 RTX 2060（4bit） | 約 0.2 Hz（1 回 2.8〜4.8 秒） | vla-arm-sim |
| 量子化後の成功率 | int4 は 71.9%（bf16 は 71.3%）、int8 は 58.1% | 論文 |
| LoRA 微調整 | 更新 1.4%、VRAM 約 27〜72GB、A100 80GB 1 枚で batch 16 が目安 | README |
| フル微調整 | A100 8 枚ノード | README |
| 高速化手法 | FAST で最大 15 倍、OFT で 25〜50 倍 | README |
| 微調整のデモ数（Franka） | 1 タスク 10〜150 デモ | 論文 |

Mac + Colima のコンテナには GPU を渡せないため、推論は別マシンの GPU サーバーに置く前提になる。

## 5. ロボットとの疎通

| 層 | 内容 | 確度 |
|---|---|---|
| VLA ⇔ ロボット側 PC | リモート推論サーバー。`deploy.py` は FastAPI の `POST /act`（`image` 256×256×3、`instruction`、`unnorm_key` を JSON で送り `{"action": 配列}` を受ける） | `deploy.py` で確認。論文は「リモート推論サーバー」とだけ記載 |
| ロボット側 PC ⇔ ロボット | 既存コントローラに差分を渡す。論文に詳細なし | 論文に記載なし |
| 制御周期 | Bridge / Franka-Tabletop は 5 Hz、Franka-DROID は 15 Hz。いずれも non-blocking | 論文 |

REST で繋がっているのは VLA とロボット側 PC の間だけで、ロボット本体への指令は別の仕組みになる。

## 6. 既存コントローラ

| ロボット | 実体 | 確度 |
|---|---|---|
| WidowX（Bridge V2） | `bridge_data_robot` の `widowx_envs`。ROS Noetic（ROS 1）+ Docker。評価クライアントは `WidowXClient(host, port=5556)` で接続し、ZMQ（`edgeml`）経由で `step_action` / `move` / `get_observation` / `reset` / `gripper` を受ける。相対動作から目標姿勢を作り、`move_to_eep()` が IK で関節角にする | コードから確認。IK の実装は未読 |
| Franka-Tabletop | 5 Hz non-blocking コントローラ。ソフト名は論文に記載なし | 未確認 |
| Franka-DROID | 15 Hz non-blocking。Polymetis かどうか、行動が関節速度か手先速度かは未確認 | 未確認 |
| Franka（ConRFT、参考） | 6 次元の手先差分を下流のインピーダンスコントローラに渡す。10 Hz | 別論文 |

## 7. 先行事例

### OpenVLA を ROS 2 に載せたもの

| 事例 | 対象 | 構成 | 結果・制約 |
|---|---|---|---|
| [vla-arm-sim](https://github.com/farouk15160/vla-arm-sim) | UR5e + MoveIt 2 + ROS 2 Jazzy（シミュレーション） | ZeroMQ（msgpack）の外部サーバー。OpenVLA-7B（4bit）の手先差分を `moveit_servo` に流す | RTX 2060 で約 0.2 Hz。未学習の UR5e では課題をこなせない。観測から実行まで約 1 秒の遅れ。MIT |
| [ワシントン大学の学位論文](https://digital.lib.washington.edu/bitstreams/9b141cdb-3317-4455-8a7a-93e40e4bfc98/download) | WidowX 250 実機 + ROS 2 + Jetson AGX Orin | OpenVLA-7B を実機評価と説明されている | ファイルが 10MB 超で中身は未確認 |

### 周辺の事例

| 事例 | 内容 | 備考 |
|---|---|---|
| [ABEJA Tech Blog](https://tech-blog.abeja.asia/entry/advent-2025-day08) | Mac M3 + ROS 2 + MuJoCo + SO-101 で SmolVLA | OpenVLA ではない。Mac は GPU をコンテナに渡せずネイティブ実行。指示の理解は限定的 |
| [Black Coffee Robotics](https://medium.com/black-coffee-robotics/vision-language-action-vla-models-llms-for-robots-f60ba0b79579) | OpenVLA-7B をシミュレーション（WidowX、Kinova）で微調整 | 実機・ROS の言及なし。単純な pick は約 100%、複雑なタスクは 5〜10%。24GB GPU で 4〜5 時間 |
| [ROS2SmolVLA](https://arxiv.org/html/2608.23320) | UR10e に SmolVLA を載せた論文 | 中身未読 |
| [awesome_vla_deployment](https://github.com/mingqian0850/awesome_vla_deployment) | VLA を実機で動かす手順の整理 | 中身未読 |
| [富士ソフト](https://www.fsi.co.jp/ros/) | Open-VLA の実機検証を自社ページに記載 | 詳細は未取得 |
| [Kan Hatakeyama の note](https://note.com/kan_hatakeyama/n/n1750880e392e) | CRANE-X7 を ROS 2 で動作テスト | VLA ではない。未読 |

## 8. このリポジトリへの示唆

| 論点 | 内容 |
|---|---|
| 構成 | GPU サーバーを別マシンに置き、ROS 2 の VLA ノードは画像を送って差分を受けるクライアントにする（`POST /act` や ZeroMQ） |
| 差分の先 | MoveIt Servo か CRANE-X7 公式ドライバに流す |
| 周期 | 5 Hz 前後。non-blocking 設計が必要 |
| DDS | HTTP / ZeroMQ で分ければ、DDS を別マシンに跨がせる必要はない |
| 新規の作業 | 「差分を受けてアームを動かす部分」が自作になる |

## 9. 未確認と次の調査

| 未確認 | 確認の手段 |
|---|---|
| ROS 2 + 実機 + Jetson での OpenVLA の実績 | ワシントン大の論文を pypdf で分割して読む |
| Franka の制御ソフト（Polymetis か、行動の種類） | DROID の `droid` / `droid_policy_learning` を読む |
| CRANE-X7 が学習データに含まれるか | Open X-Embodiment のデータセット一覧 |
| CRANE-X7 向けの追加学習に必要なデータ量 | 公式に記載なし。Franka の 10〜150 デモが目安 |
| Llama Community License の商用条件、OFT / FAST のライセンス | 各ライセンス原文 |
| `awesome_vla_deployment` と ROS2SmolVLA の内容 | 原文を読む |
