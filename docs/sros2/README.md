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
docker compose build ros2lab-a            # 診断コンテナは ros2lab:jazzy を土台にするので、先にこれを作っておく
docker compose --profile diag up -d --build ros2diag
bash scripts/verify-env.sh c              # その環境になっていることを確認する
bash scripts/env-status.sh                # 起動中のコンテナの環境を表示
bash scripts/sros2/check-ledger.sh        # 台帳と期待結果の検査（環境 b の元データ）
```

`verify-env.sh` の結果と証拠（pcap・ログ）は `.artifacts/sros2-verify/<env>-<日時>/` に残る。
`WIPE_ROGUE=1 bash scripts/verify-env.sh c` なら、終了時に不正証明書も削除する。pcap には証明書や
（環境 a / b では）平文の payload が入るので、不要になったら `.artifacts/` ごと削除してよい。

- 環境 a: 鍵なしの診断機から見える・受信できる、pcap に payload が平文で出る
- 環境 c: 鍵なしの参加者と不正証明書 4 種（期限切れ・想定外 CA・自己署名・失効済み）が拒否される、
  有効期間・CRL・governance・permissions・鍵の分離とパーミッションが設計どおり
- 環境 b: 台帳の全項目について、不備が実際に注入されていること（`ground_truth_probe`）

## 環境 a に戻す

```bash
bash scripts/up-env.sh a                  # ros2lab-a/b を SROS2 なしで作り直す（環境 b の鍵のコピーも削除する）
docker compose --profile diag rm -sf ros2diag   # 診断コンテナだけを止める（down は ros2lab-a/b も落とすので使わない）
bash scripts/sros2/wipe-rogue.sh          # 不正証明書を削除する
```

`sros2/keystores/` と `sros2/ca-private/` は残る（gitignore 済み）。不要なら手で削除してよい。
`ros2arm` を使うときは、環境 a に戻したあとに `bash scripts/up-arm.sh` で起動し直す。
`docker compose up` を直接叩くと、ros2lab-a/b は環境 a で作り直される（SROS2 のコンテナは `up-env.sh` が作る）。
そのとき環境 b が `./workspace` に置いた鍵のコピーは削除されないので、`up-env.sh a` を使うか、手で削除すること。
環境 c への切替が失敗したときも、コピーが残ることがある。`verify-env.sh c` の C12 が検出するので、残っていたら
`./workspace/sros2-keystore` を手で削除する。
環境 b に切り替えるときは、先に `bash scripts/sros2/gen-keystore.sh b` で作り直す
（環境 a / c に切り替えると、環境 b が `./workspace` に置いた鍵のコピーは削除されるため）。

## 鍵・証明書の扱い

- 生成物は `sros2/keystores/<env>/`（コンテナに渡す、自分の enclave だけ）、`sros2/ca-private/<env>/`
  （CA の秘密鍵と発行台帳。**コンテナには渡さない**）、`sros2/rogue/<env>/`（診断用の不正証明書）。
  すべて gitignore 済みで、コミットしない。
- コンテナには自分の enclave だけを read-only で渡し、起動時にコンテナ内の tmpfs（共有されない）へ
  `0700/0600` でコピーする（macOS の bind mount ではパーミッションを制御できないため）。
- **更新:** 証明書は 90 日、CRL は 180 日、identity CA は 365 日で期限が切れる（CA を含めて作り直す）。
  `gen-keystore.sh c` で再生成し、`up-env.sh c` でコンテナを作り直す。
  CRL の期限（nextUpdate）が切れると、検証する側がすべての参加者を拒否する。
  **症状:** 期限が切れても明確なエラーは出ず、discovery で相手が見えなくなる（静かに通信できなくなる）。
  `verify-env.sh c` が、証明書と CRL の残り有効期間が 14 日未満なら FAIL にする（C15）。
- **鍵を再生成したあと:** `gen-keystore.sh` は keystore のディレクトリを入れ替えるので、起動中のコンテナは古い鍵のまま
  動き続けるか、mount が空に見える。`docker compose restart` では足りず、必ず `bash scripts/up-env.sh <env>` で作り直す。
  再生成の途中に `restart` すると、鍵が無いためコンテナは起動に失敗する（鍵なしでは動かさない）。
- **鍵が揃っていないとき:** SROS2 が有効なコンテナ（環境 b / c）は、鍵が渡されていない・欠けているときは
  起動に失敗する（鍵なしのまま動き続けない）。`gen-keystore.sh` の途中で失敗しても、既存の keystore は壊れない
  （一時ディレクトリに作ってから入れ替える）。
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
- 鍵は `/sros2/src`（read-only の bind mount）にも見える。macOS の bind mount ではホスト側のパーミッションを
  コンテナから制御できないため、パーミッションの保証は tmpfs 側（`/run/sros2`）だけに限られる。
  `verify-env.sh c` の C12 は「見える秘密鍵が自分の enclave だけ（tmpfs と /sros2/src の 2 か所）」を確認する。
- `verify-env.sh` の環境 a の検査は A1〜A4（環境ラベル・SROS2 の設定がないこと・鍵なしの診断機から見えること・平文）で、
  期待結果の他の判定（保護がないので FAIL）は、保護機能が存在しないことから成り立つものとして扱う。
- 公開してはいけない語の検査（`check-ledger.sh`）は、社内資料由来の語を局所ファイル
  （`.plans/sros2-env-abc/banned-terms.txt`。コミットしない）から読むため、CI では一般的なパターン
  （個人パス・私的アドレス・外部サービスの URL）だけを検査する。社内資料由来の語は、手元の検査でだけ強く検査できる。
- 統合検証（`verify-env.sh`）は Docker と ros の実イメージが必要で CI では動かない。CI が検査するのは、シェルの静的解析・
  起動スクリプトのガード・entrypoint・台帳の検査（変異テスト `check-ledger.sh --selftest` を含む）・compose の構文。
  実コンテナでの結果は PR 本文に記録する。
- ros2real（実機）は対象外。実機に適用する場合は、鍵・governance・permissions の生成スクリプト
  （`scripts/sros2/lib/gen-keystore-inner.sh` は `ros2` CLI と `openssl` だけに依存する）を使えるが、
  固定・人の立入禁止・手元の非常停止・閉域ネットワーク・無害な値という安全条件を満たしてから行う。
- 別の compose project のコンテナ（同じ `ros2lab-a` などの名前）が残っていると起動できない。
  `verify-env.sh` はコンテナ名を `LAB_A` / `LAB_B` / `DIAG` で差し替えられる。

## 関連

- [台帳](defect-ledger.md) / [実測メモ](spike-results.md) / [認証ハンドシェイクの観測](handshake-observation.md)
