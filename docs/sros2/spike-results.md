# SROS2 実測メモ（Fast DDS 2.14.6 / sros2 0.13.6）

SROS2 の環境（無防備 / 誤設定 / 適正設定）を作る前に、`ros2lab:jazzy` イメージ上で実測した結果。
推測ではなく実測値だけを書く。バージョンが違えば結果も変わりうるので、実機などで使う場合は差分を再確認すること。

実測は使い捨てコンテナ（`--network none`、`ROS_DOMAIN_ID=42`、`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`）で行った。

## 確認できたこと

| # | 事実 | 実測 |
|---|---|---|
| 1 | 認証済み同士は通信でき、鍵なしの参加者は Enforce の相手から 0 件 | 受信 25〜33 件 / 鍵なし 0 件 |
| 2 | `generate_artifacts` の governance は、生成時の `ROS_DOMAIN_ID` が domain id に入る。既定は `allow_unauthenticated_participants=false`、RTPS は `SIGN` | 42 で生成しないと Domain 42 では効かない |
| 3 | sros2 が発行する証明書の有効期間は 3650 日固定 | `sros2/_utilities.py` と実測の `notAfter` |
| 4 | permissions の有効期間は証明書の有効期間を継承する | 証明書を 90 日で再発行して `create_permission` すると 90 日になる |
| 5 | `create_keystore` の既定では identity CA と permissions CA が同一（`ca.key.pem` へのシンボリックリンク） | `ls -l` |
| 6 | sros2 の既定では鍵ファイル（CA と enclave の秘密鍵）が `0644` で作られる | `ls -l` |
| 7 | enclave ディレクトリ内の `governance.p7s` などはシンボリックリンク | 別の場所へ渡すときは実体にコピーする（`cp -L`） |
| 8 | 不正証明書: 別 CA は拒否、自己署名は拒否 | 受信 0 件 |
| 9 | 期限切れ証明書は、提示する側が起動時に自分で失敗する。期限内に起動し、期限後に参加者を入れると拒否される | 期限前 15 件 / 期限後 0 件 |
| 10 | 失効済み証明書は、CRL を設定しないと受理される | 受信 32 件 |
| 11 | rmw_fastrtps は XML プロファイルのプロパティを丸ごと上書きするため、XML の `identity_crl` は効かない | `participant.cpp` の読み取りと実測（CRL を指定しても受理） |
| 12 | enclave ディレクトリに `crl.pem` を置くと、検証する側が失効を拒否する。壊れた CRL は起動に失敗する | 通常 33 件 / 失効 0 件 |
| 13 | CRL の `nextUpdate` は検証側で効く（過ぎるとすべて拒否される OpenSSL の挙動）。CRL は証明書より長く有効にして更新手順を持つ | 設計上の注意 |
| 14 | `ROS_SECURITY_STRATEGY=Permissive` で enclave 名が不一致だと、セキュリティなしにフォールバックして動く。`Enforce` は起動に失敗する | Permissive: 鍵なしの受信側が 31 件受信 / Enforce: 起動失敗 |
| 15 | `allow_unauthenticated_participants=true` は RTPS 保護が `NONE` でないと、認証済みの参加者自身が起動に失敗する | RTPS `ENCRYPT` / `SIGN` で失敗、`NONE` で起動 |
| 16 | rclpy ノードは、ポリシーに `rosout`・`parameter_events`・type description サービスのトピックがないと Enforce で起動に失敗する | 検証用ノードは `enable_rosout=False`、`-p start_type_description_service:=false`、`parameter_events` を policy に追加 |

## 確認できなかったこと

- `allow_unauthenticated_participants=true`（RTPS `NONE`、discovery/liveliness `NONE`、対象トピックを保護なしにした場合）でも、鍵なしの参加者はこの構成では認証済みの参加者を発見できなかった（EDP のエラー）。
  原因は未特定。governance の設定の有無は静的に確認できるが、「鍵なしの参加者が実際に入れる」ことは、この環境では確認できていない。

## 参考（公開情報）

- Fast DDS の CRL 検証は、`identity_crl` プロパティで読んだ CRL があると `X509_V_FLAG_CRL_CHECK` が有効になる（Fast-DDS v2.14.6 `src/cpp/security/authentication/PKIDH.cpp`）。
- rmw_fastrtps は enclave ディレクトリの `crl.pem` を `identity_crl` として渡す（`rmw_fastrtps_shared_cpp/src/participant.cpp`、`rmw_dds_common/src/security.cpp`）。
