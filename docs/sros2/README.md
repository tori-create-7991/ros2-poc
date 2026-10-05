# SROS2 の環境 A / B / C

ros2lab-a / ros2lab-b で SROS2（DDS-Security）の環境を切り替えて、診断ツールの検出精度を測るための
「正解が分かっている環境」を作る。ros2real（実機）と ros2arm（シミュ）は対象外（下の「制約」参照）。

| 環境 | 内容 | 診断ツールに期待する結果 |
|---|---|---|
| a 無防備 | SROS2 未適用（ROS 2 の既定） | 保護がないので、ほぼ全観点が不合格 |
| b 誤設定 | SROS2 適用済み + [台帳](defect-ledger.md) の不備を意図的に注入 | 注入した不備をすべて検出できる（見逃しを数える） |
| c 適正設定 | 推奨どおり（下表）。不合格ゼロ | 不合格を出したら誤検知（誤検知を数える） |

期待する判定は `sros2/ledger/expected-results.yaml`、環境 b の不備は `sros2/ledger/ledger.yaml` が唯一の出所になる。

## 環境 c の設定

| 項目 | 設定 |
|---|---|
| ROS_SECURITY_STRATEGY | `Enforce` |
| governance | 未認証参加者を許可しない、RTPS・discovery・データをすべて暗号化、アクセス制御あり |
| permissions | 最小権限（コンテナごとに必要なトピックだけ。ワイルドカードなし、default は DENY） |
| 鍵 | コンテナごとに別の enclave。identity CA と permissions CA は分離。秘密鍵はコンテナ内の tmpfs に `0600` |
| 証明書 | 有効期間 90 日 |
| CRL | 全 enclave に `crl.pem`（失効エントリ入り。有効期間 180 日） |

## 使い方

```bash
bash scripts/sros2/gen-keystore.sh c      # b / c の keystore を生成（初回と、証明書・CRL を更新するとき）
bash scripts/up-env.sh c                  # 環境を切り替えて ros2lab-a/b を起動し直す（a|b|c）
docker compose --profile diag up -d --build ros2diag
bash scripts/verify-env.sh c              # その環境になっていることを確認する
bash scripts/env-status.sh                # 起動中のコンテナの環境を表示
bash scripts/sros2/check-ledger.sh        # 台帳と期待結果の検査（環境 b の元データ）
```

`verify-env.sh` の結果と証拠（pcap・ログ）は `.artifacts/sros2-verify/<env>-<日時>/` に残る。

- 環境 a: 鍵なしの診断機から見える・受信できる、pcap に payload が平文で出る
- 環境 c: 鍵なしの参加者と不正証明書 4 種（期限切れ・想定外 CA・自己署名・失効済み）が拒否される、
  有効期間・CRL・governance・permissions・鍵の分離とパーミッションが設計どおり
- 環境 b: 台帳の全項目について、不備が実際に注入されていること（`ground_truth_probe`）

## 鍵・証明書の扱い

- 生成物は `sros2/keystores/<env>/`（コンテナに渡す、自分の enclave だけ）、`sros2/ca-private/<env>/`
  （CA の秘密鍵と発行台帳。**コンテナには渡さない**）、`sros2/rogue/<env>/`（診断用の不正証明書）。
  すべて gitignore 済みで、コミットしない。
- コンテナには自分の enclave だけを read-only で渡し、起動時にコンテナ内の tmpfs（共有されない）へ
  `0700/0600` でコピーする（macOS の bind mount ではパーミッションを制御できないため）。
- **更新:** 証明書は 90 日、CRL は 180 日で期限が切れる。`gen-keystore.sh c` で再生成し、`up-env.sh c` で
  コンテナを作り直す。CRL の期限（nextUpdate）が切れると、検証する側がすべての参加者を拒否する。
- **不正証明書の流出防止:** `ros2diag` は `ros2-lab-net`（internal）だけに接続し、ポートも `./workspace` も
  渡さない。不正証明書は `ros2diag` にだけ read-only で渡し、ros2lab-a/b には渡さない。
  不要になったら `bash scripts/sros2/wipe-rogue.sh` で削除する。期限切れの証明書は、時間に依存するため
  `bash scripts/sros2/gen-rogue.sh c expired 40` で試験の直前に作る（`verify-env.sh c` が自動で行う）。

## 制約

- DDS 実装は Fast DDS（`rmw_fastrtps_cpp`）。実測は Fast DDS 2.14.6 / sros2 0.13.6。バージョンが違えば
  挙動が変わりうる（[実測メモ](spike-results.md)）。Cyclone DDS は対象外。
- ros2arm は SROS2 化していない。環境 b / c と同時には起動できず、カメラプロファイルなどは環境 a で使う。
  ros2arm の SROS2 化は Gazebo / MoveIt 2 のトピックが多く、最小権限の permissions を `ros2 security generate_policy`
  で実際のグラフから生成する作業が別に要る。
- enclave はコンテナ単位。同じコンテナの複数プロセスは同じ enclave を共有する。
- ros2real（実機）は対象外。実機に適用する場合は、鍵・governance・permissions の生成スクリプト
  （`scripts/sros2/lib/gen-keystore-inner.sh` は `ros2` CLI と `openssl` だけに依存する）を使えるが、
  固定・人の立入禁止・手元の非常停止・閉域ネットワーク・無害な値という安全条件を満たしてから行う。
- 別の compose project のコンテナ（同じ `ros2lab-a` などの名前）が残っていると起動できない。
  `verify-env.sh` はコンテナ名を `LAB_A` / `LAB_B` / `DIAG` で差し替えられる。

## 関連

- [台帳](defect-ledger.md) / [実測メモ](spike-results.md) / [認証ハンドシェイクの観測](handshake-observation.md)
