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
  動き続けるか、mount が空に見えることがある。再生成のあとは必ず `bash scripts/up-env.sh <env>` で作り直す
  （`docker compose restart` が新しい鍵を読むかどうかは mount の実装に依存するため、期待しない）。
  再生成の途中に `restart` すると、鍵が無いためコンテナは起動に失敗する（鍵なしでは動かさない）。
- **生成が途中で失敗・中断したとき:** 入れ替えの途中で失敗したり、Ctrl-C などで中断されたりしたときは、
  元の keystore / ca-private / rogue に自動で戻す。強制終了（`kill -9` など）だけは戻せない。
  そのときは `sros2/.old-<env>`（と `sros2/.lock-<env>`、`sros2/.stage-<env>`）が残り、次の生成は旧データを消さずに中止する。
  keystore は再生成できるので、**旧い鍵を保つ必要がなければ、これらを削除して `gen-keystore.sh` をやり直す**
  （`.lock-<env>` は、別の生成が動いていないことを確認してから削除する）。
  削除した直後に同じエラーが出ることがある（Colima のファイル共有のキャッシュで、コンテナからまだ見えているため）。
  数秒待ってから再実行する。
  中身を手で戻すのは勧めない（`.old-<env>` の中は `ks` / `ca` / `rogue` で、戻し先の名前と違い、
  新旧が混在していることもあるため）。
- **鍵が揃っていないとき:** SROS2 が有効なコンテナ（環境 b / c）は、鍵が渡されていない・欠けているときは
  起動に失敗する（鍵なしのまま動き続けない）。`gen-keystore.sh` の途中で失敗しても、既存の keystore は壊れない
  （一時ディレクトリに作ってから入れ替える）。
- **不正証明書の流出防止:** `ros2diag` は `ros2-lab-net`（internal）だけに接続し、ポートも `./workspace` も
  渡さない。不正証明書は `ros2diag` にだけ read-only で渡し、ros2lab-a/b には渡さない。
  不要になったら `bash scripts/sros2/wipe-rogue.sh` で削除する。期限切れの証明書は、時間に依存するため
  `bash scripts/sros2/gen-rogue.sh c expired 40` で試験の直前に作る（`verify-env.sh c` が自動で行う）。

## ros2arm / ros2server（アームシミュと VLA 連携）

`ros2lab-a/b` と同じ仕組み（enclave 単位の鍵、`sros2/entrypoint.sh`、`docker-compose.sros2.yml`、`gen-keystore.sh`）で、
`ros2arm`（Gazebo / MoveIt 2 / RViz / 仮想カメラ）と `ros2server`（VLA ノード・変換ノード）も環境 b / c で動く。

```bash
bash scripts/sros2/gen-keystore.sh c
bash scripts/up-env.sh c --arm --vla     # ros2lab-a/b と ros2arm・ros2server・vla-server を環境 c で起動し直す
# 注意: SROS2 の環境（b / c）では scripts/up-arm.sh を使わない（素の docker compose で ros2arm が環境 a で作り直され、混在する。
#       ros2lab-a が b / c のときは up-arm.sh が案内して止まる）。環境 a に戻すときも --arm --vla を付ける
# ros2arm のデスクトップ（noVNC）の端末、または docker exec -u ubuntu ros2arm で、シミュを起動する（環境変数は自動で入る）
bash scripts/run-vla.sh --instruction "move down" --steps 2
```

`ros2lab` と違う点（`docker-compose.sros2.yml` のコメントにも書いてある）:

- **ベースイメージの entrypoint に引き継ぐ**: ros2arm のベースは `/entrypoint.sh`（supervisord でデスクトップを起動）。
  `SROS2_NEXT=/entrypoint.sh` で、鍵を置いたあとに渡す。
- **鍵の所有者**: ノードは root ではなく `ubuntu`（uid 1000）で動く。`SROS2_CHOWN=1000:1000` と tmpfs の `uid=1000,gid=1000` で
  `ubuntu` だけが読めるようにする（モードは 0700 / 0600 のまま）。
- **端末の環境変数**: デスクトップの端末はコンテナの ENV を継承しない。`SROS2_ENV_FILE` が書く環境ファイル（`ROS_SECURITY_*`。
  鍵は含まない）を `~/.bashrc` が読む。読まないと、端末から起動したノードだけセキュリティなしで動いてしまう。

### 環境 c のポリシー（最小権限、生成して使う）

`ros2arm` と `ros2server` は 1 コンテナ 1 enclave（`ROS_SECURITY_ENCLAVE_OVERRIDE`）で多数のノードが共有するので、
permissions はノードの和集合になる。`sros2/policy/lab-c.xml` の `/lab/ros2arm` と `/lab/ros2server` は、
稼働中のグラフ（`ros2 security generate_policy`）と、そのポリシーで動かしたときの Fast DDS の拒否ログから
[`scripts/sros2/lib/sim_policy.py`](../../scripts/sros2/lib/sim_policy.py) で生成した（手順はそのファイルの先頭）。

- 実在するトピック・サービス名を列挙している（ワイルドカードなし）。
- 例外: 起動のたびに名前が変わるノード（`moveit_<数字>`、`transform_listener_impl_<ハッシュ>`、ros2 CLI の `_ros2cli_<数字>`）の標準の
  パラメータ・ログ系サービス（`describe_parameters` など）だけ `*/<サービス名>` のパターン。ノード名ごとの列挙は次の起動で外れるため。
- 稼働中のグラフには、短命のプロセス（controller の `spawner`）や起動時だけ作られるクライアントが載らない。拒否ログを
  `sim_policy.py` の `--denials` に渡して足りない分を足し、拒否が無くなるまで繰り返した（実測は下）。
- 生成の入力は `sros2/policy/sim-inputs/`（稼働中のグラフ `live-graph.xml` と、拒否ログから足した分 `denials.txt`）にコミットしてある。
  `ros2_poc_sim/test/test_sim_policy.py` が、この入力から再生成した結果と `lab-c.xml` の一致を検査する（手で編集して食い違うと CI が落ちる）。
- グラフに現れない（オンデマンドで作られる）エンドポイントは拒否され、そのノードが落ちる。見つかったら次の手順で生成し直す。

**ポリシーが足りないとき（症状 → 確認 → 再生成）**

```bash
# 1. 症状: 環境 c で ros2arm のノードが "process has died" で落ちる、または新しい機能が動かない。
#    シミュを起動した端末の出力（ファイルに残すなら `ros2 launch ... > sim.log 2>&1`）に Fast DDS の拒否が出ている:
grep -E "not found in allow rule" sim.log | head                      # 例: rr/controller_manager/load_controllerReply topic not found in allow rule
# 2. 拒否を足す。sim_policy.denials() が読める形（<publish|subscribe|reply|request>:<名前>）にして、
#    sros2/policy/sim-inputs/denials.txt に足す（ros2arm / ros2server どちらのログも同じ形式）:
python3 -c "import sys; sys.path.insert(0, 'scripts/sros2/lib'); import sim_policy as S; print(*[f'{v}:{n}' for v, n in S.denials('sim.log')], sep='\n')"
# 3. sim_policy.py の先頭に書いた 2 つのコマンドで enclave を作り直し、sros2/policy/lab-c.xml の該当部分を置き換える
# 4. 反映: bash scripts/sros2/gen-keystore.sh c && bash scripts/up-env.sh c --arm --vla（反映後に拒否が 0 になるまで繰り返す）
```
- `run-vla.sh --record` の記録（`ros2arm` 内の `scenario_observer`）が購読する `/camera/color/image_raw` と `/camera/color/camera_info` は、稼働中のグラフ（ros2arm は発行側）に購読として載らないので `denials.txt` に静的に足した（`ros2arm` / `ros2server` の enclave を再生成。`ros2server` はもともと画像を購読）。**実コンテナでの確認は未実施**。拒否が出たらこの手順で足す。
- `ros2lab-a/b` の権限（トピック 6 件、ワイルドカードなし、default DENY）は変わらない。`verify-env.sh c` の C10 はこちらを見る。
- 環境 c の `ros2arm` に ROS CLI（`ros2 topic hz` など）で入っても、CLI の購読は許可されていない（`ros2server` からは許可済みの範囲で使える）。
- **鍵の読み取り**: `ros2arm` の VNC デスクトップ・noVNC の端末・Gazebo / MoveIt など全プロセスが同じ `ubuntu`（uid 1000）で動き、
  enclave は 1 つを共有するので、端末からも自分の enclave の鍵は読める（ノード単位の分離は無い）。CA の秘密鍵と他コンテナの鍵は見えない。
  `verify-env.sh` の C12 / C13 は `ros2lab-a` だけを検査し、`ros2arm` / `ros2server` の鍵の所有者・モードは検査対象に入れていない。
- `*/set_parameters` と `*/set_parameters_atomically`（書き込み系）も、名前が変わるノードのためにパターンで許可している。読み取り系だけに絞れるかは未検討。

実測（Colima 4 CPU、`ros2arm` は CPU 2 つに制限、Fast DDS 2.14.6）:

| 項目 | 結果 |
|---|---|
| 環境 c（Enforce + ENCRYPT）でのシミュ起動 | 約 1 分でコントローラとカメラの準備まで完了。最小権限のポリシーで拒否 0、プロセス死亡 0（反復の最後のラウンド） |
| 負荷（セキュリティなしとの比較） | カメラ約 0.8 Hz と約 0.6 Hz、`/joint_states` 約 85 Hz と約 69 Hz、RTF 0.5〜0.9 と 0.25〜0.5。差はノイズの範囲で、ENCRYPT による目立った劣化は見えない（どちらも CPU 描画で律速） |
| `ros2server` ⇄ `ros2arm`（`run-vla.sh --steps 2`） | 環境 c で 2 ステップとも ok（約 40 秒。探索の `--spin-time 15` を含む） |
| 鍵なしの参加者 | 見えない（ノード 0）。セキュリティが効いている |

実測で分かった注意:

- **探索に時間がかかる**: SROS2 では Discovery に認証のやり取りが入り、`ros2 topic info` などの CLI は既定の待ち時間では何も見えない
  （`--spin-time 15` で見える）。`run-vla.sh` は環境 a 以外で自動的に延ばす。
- **画像の購読は RELIABLE**: 約 900KB の画像を暗号化して別コンテナへ送るとき、BEST_EFFORT では落ちた断片が再送されず、
  フレームが 1 枚も完成しないことがあった。`vla_node` は既定で RELIABLE（仮想カメラの QoS が reliable）。
- **短命の ROS プロセスを大量に作ると詰まることがある**（原因は未確認。仮説は、購読者が増えるたびに認証のやり取りが走る負荷）。
  再現条件の記録: 環境 c、`ros2arm` は CPU 2 つに制限、シミュを約 40 分動かす間に、`ros2 topic info` / `hz` や使い捨ての Python 購読を
  合計で 50 回ほど起動・終了した後。症状は、`ros2server` で新しく起動した購読が `/camera/color/image_raw`（RELIABLE、約 0.9 Hz）を 0 枚しか受け取れず、
  何も購読しない参加者を 1 つ足すだけでも同じ（先に起動していた購読も止まり、足した参加者が終わると再開した）。
  シミュを起動し直すと、同じ操作で正常に戻った（A 126 枚・B 54 枚）。環境 c では、手で ROS CLI を連打せず、詰まったらシミュを起動し直す。

## 制約

- DDS 実装は Fast DDS（`rmw_fastrtps_cpp`）。実測は Fast DDS 2.14.6 / sros2 0.13.6。バージョンが違えば
  挙動が変わりうる（[実測メモ](spike-results.md)）。Cyclone DDS は対象外。
- ros2arm / ros2server は `up-env.sh --arm --vla` で環境 a / b / c に対応する（下の「ros2arm / ros2server」）。
  ros2real（実機）は SROS2 化していない。
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
