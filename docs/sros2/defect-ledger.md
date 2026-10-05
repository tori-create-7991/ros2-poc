# 環境 B 仕込み不備台帳

<!-- このファイルは sros2/ledger/ledger.yaml から生成される。手で編集しないこと。
     再生成: bash scripts/sros2/check-ledger.sh --render -->

- 版: **draft**（draft の間は項目を追加・削除できる。frozen にしたら分母を動かさない）
- DDS 実装: Fast DDS 2.14.6 / sros2 0.13.6（ros:jazzy イメージ）
- 注入: `scripts/sros2/lib/gen-keystore-inner.sh`（`setup_b`）。注入できているかは `bash scripts/verify-env.sh b` が確認する
- 根拠の grade: **A** = 一次情報（ソース・実測）で確認済み / **B** = 公開資料の記述（要約で確認。原典は未精読） / **C** = 要追加

| ID | 不備 | 注入の方法 | 観点（check） | 観測性 | 影響 | 連動 |
|---|---|---|---|---|---|---|
| B-AU-01 | 未認証参加者の許可 | governance の allow_unauthenticated_participants=true（Fast DDS は RTPS 保護が NONE でないと起動しない） | `unauthenticated-join` | network | なりすまし | B-CR-02 |
| B-AU-02 | Permissive のまま稼働（enclave 不一致でセキュリティなしにフォールバック） | ROS_SECURITY_STRATEGY=Permissive、ros2lab-b の enclave 名を 1 文字間違える（shared → shred） | `unauthenticated-join` | network | なりすまし | - |
| B-AU-03 | 全コンテナで同一の証明書・鍵 | ros2lab-a/b が同じ enclave（/lab/shared）の証明書と秘密鍵を使う | `credential-uniqueness` | network | なりすまし | - |
| B-AU-04 | 実質無期限の証明書 | sros2 が発行する証明書をそのまま使う（有効期間 3650 日） | `credential-lifecycle` | network | なりすまし | - |
| B-AU-05 | 失効手段なし（CRL 未配置。失効済みの証明書が受理される） | enclave に crl.pem を置かない | `cert-validation` | network | なりすまし | - |
| B-AU-06 | identity CA と permissions CA が同一 | sros2 の既定のまま（create_keystore が 1 つの CA を両方に使う） | `ca-structure` | network | なりすまし | - |
| B-AU-07 | 秘密鍵が共有領域にあり、誰でも読める | 全コンテナから書き込める ./workspace に鍵つきの keystore のコピーを置く。鍵は 0644（sros2 の既定の権限） | `key-storage` | host | なりすまし | - |
| B-CR-01 | 制御指令トピックだけ保護対象外 | governance の topic_rule で rt/lab/cmd を data_protection_kind=NONE・アクセス制御なしにする | `protection-scope` | network | 情報漏えい | - |
| B-CR-02 | RTPS 保護が NONE | governance の rtps_protection_kind=NONE（B-AU-01 を成立させるために必要。連動） | `protection-scope` | network | 情報漏えい | B-AU-01 |
| B-AC-01 | permissions にワイルドカード | permissions の topic に * を使い、実質すべてのトピックを許可する | `permission-least-privilege` | network | なりすまし | - |
| B-AC-02 | permissions の default が ALLOW | 署名済みの permissions の default を ALLOW に書き換えて署名し直す | `permission-least-privilege` | network | なりすまし | - |

## 根拠

### B-AU-01 未認証参加者の許可

- [A] docs/sros2/spike-results.md — RTPS 保護が ENCRYPT/SIGN だと認証済み参加者自身が起動に失敗し、NONE で起動する（実測）
- [B] https://docs.ros.org/en/iron/Concepts/Intermediate/About-Security.html — governance の allow_unauthenticated_participants の説明
- 注: 鍵なし参加者が実際に入れるかは、この環境では確認できていない（docs/sros2/spike-results.md の「確認できなかったこと」）。設定の有無は静的に確認できる。

### B-AU-02 Permissive のまま稼働（enclave 不一致でセキュリティなしにフォールバック）

- [A] docs/sros2/spike-results.md — Permissive で enclave 名が不一致だとセキュリティなしで動き、鍵なしの受信側が受信する。Enforce は起動に失敗する（実測）
- [B] https://docs.ros.org/en/iron/Concepts/Intermediate/About-Security.html — Permissive は違反をログに出すだけで通す。デバッグ用

### B-AU-03 全コンテナで同一の証明書・鍵

- [A] docs/sros2/handshake-observation.md — 認証ハンドシェイクで証明書は平文で交換され、ネットワーク上で収集できる
- [B] https://arxiv.org/abs/1908.05310 — 既定プラグインが参加者の権限情報を平文で交換する点の分析

### B-AU-04 実質無期限の証明書

- [A] sros2 0.13.6 sros2/_utilities.py — 証明書の有効期間が 3650 日固定（コンテナ内のソースと実測の notAfter）

### B-AU-05 失効手段なし（CRL 未配置。失効済みの証明書が受理される）

- [A] docs/sros2/spike-results.md — CRL なしでは失効済み証明書が受理され、enclave に crl.pem を置くと拒否される（実測）

### B-AU-06 identity CA と permissions CA が同一

- [A] docs/sros2/spike-results.md — create_keystore の既定では identity CA と permissions CA が同一の ca.key.pem（シンボリックリンク）

### B-AU-07 秘密鍵が共有領域にあり、誰でも読める

- [A] docs/sros2/spike-results.md — sros2 の既定では鍵ファイルが 0644 で作られる（ls -l）
- [A] README.md — ./workspace は ros2lab-a/b・ros2arm・ros2real で共有され、書き込み可

### B-CR-01 制御指令トピックだけ保護対象外

- [B] https://nvd.nist.gov/vuln/detail/CVE-2019-19625 — SROS2 0.8.1 の既定の governance が node 情報を漏らした事例（保護範囲の設定不備）

### B-CR-02 RTPS 保護が NONE

- [A] docs/sros2/spike-results.md — allow_unauthenticated_participants=true は RTPS 保護 NONE でないと起動しない（実測）

### B-AC-01 permissions にワイルドカード

- [B] https://docs.ros.org/en/iron/Tutorials/Advanced/Security/Access-Controls.html — permissions による最小権限の設定の説明

### B-AC-02 permissions の default が ALLOW

- [A] sros2 0.13.6 の生成する permissions.xml — sros2 は default を DENY で生成する（実測）。ALLOW への変更は手動の誤設定

## 環境ごとの期待判定

| 観点（check） | 意味 | 環境 A | 環境 B | 環境 C |
|---|---|---|---|---|
| `unauthenticated-join` | 資格情報のない参加者が加われないこと | FAIL | FAIL (B-AU-01, B-AU-02) | PASS |
| `cert-validation` | 不正な証明書（期限切れ・想定外 CA・自己署名・失効済み）を受け付けないこと | FAIL | FAIL (B-AU-05) | PASS |
| `credential-uniqueness` | 資格情報（証明書・鍵）がコンテナごとに異なること | FAIL | FAIL (B-AU-03) | PASS |
| `credential-lifecycle` | 証明書に妥当な有効期限があり、失効の手段があること | FAIL | FAIL (B-AU-04) | PASS |
| `ca-structure` | identity CA と permissions CA が分かれていること | FAIL | FAIL (B-AU-06) | PASS |
| `key-storage` | 秘密鍵が保護された場所・権限にあること（ホストの検査が必要） | N/A | FAIL (B-AU-07) | PASS |
| `protection-scope` | 通信（RTPS・discovery・データ）が保護されていること | FAIL | FAIL (B-CR-01, B-CR-02) | PASS |
| `permission-least-privilege` | permissions が最小権限であること | FAIL | FAIL (B-AC-01, B-AC-02) | PASS |

## 台帳の凍結と追加の運用

- 台帳は `version: draft` の間だけ項目を追加・削除できる。
- 検出精度の分母として使い始めるときに `sros2/ledger/ledger.yaml` の `version` を `frozen` にする。凍結後は項目を動かさない。
- 凍結後に追加したい不備は、別の台帳（v1.1）として管理し、凍結した台帳の分母は変えない。
- 連動する不備（`coupled_with`）を分母で 1 件と数えるか 2 件と数えるかは、凍結する前に決めておく。
- ホストの検査が必要な不備（`observability: host`）を分母に含めるかも、凍結する前に決めておく。
