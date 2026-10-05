"""台帳（sros2/ledger/ledger.yaml）と期待結果（expected-results.yaml）の検査と、人が読む版の生成。

  python3 ledger.py check <リポジトリのルート>     検査（失敗なら終了コード 1）
  python3 ledger.py render <リポジトリのルート>    docs/sros2/defect-ledger.md の内容を標準出力へ
  python3 ledger.py list <リポジトリのルート>      台帳の「ID<TAB>probe 名<TAB>題名」を 1 行ずつ標準出力へ（verify-env.sh が読む）

検査する内容:
  - 必須欄、ID の形式と重複、列挙値、根拠が 1 件以上あること
  - coupled_with が存在する ID を指していること
  - ground_truth_probe が scripts/verify-env.sh に関数として存在すること
  - expected-results.yaml の環境 b が台帳と矛盾しないこと（台帳の check が FAIL で、because に ID があること）
  - 公開リポジトリに置いてはいけない語（個人パス・私的アドレス・外部サービスの URL。社内資料由来の名称は局所ファイルから読む）が含まれないこと
  - docs/sros2/defect-ledger.md が台帳から生成した内容と一致すること（ファイルがある場合）
"""
import os
import re
import sys
from pathlib import Path

import yaml

REQUIRED = [
    "id", "title", "injected_by", "check", "expected_verdict", "observability",
    "behavior_impact", "ground_truth_probe", "evidence",
]
VERDICTS = {"PASS", "FAIL", "N/A"}
OBSERVABILITY = {"network", "host"}
IMPACTS = {"停止", "誤動作", "情報漏えい", "なりすまし"}
GRADES = {"A", "B", "C"}
ID_RE = re.compile(r"^B-(AU|CR|AC)-\d\d$")

# 公開リポジトリに置かない語（AGENTS.md）。ここには一般的なパターンだけを書く。
# 社内資料由来の名称などは、このリポジトリにも書いてはいけないので、コミットされない局所ファイル
# （既定は .plans/sros2-env-abc/banned-terms.txt。1 行 1 正規表現。環境変数 LEDGER_BANNED_FILE で変更可）から読む。
BANNED = [
    r"drive\.google", r"notion\.(so|com)", r"192\.168\.", r"/Users/[A-Za-z]",
]


def banned_patterns(root: Path) -> list:
    path = Path(os.environ.get("LEDGER_BANNED_FILE", root / ".plans/sros2-env-abc/banned-terms.txt"))
    extra = []
    if not path.exists():
        print("注: 追加の禁止語ファイルが無いため、一般的なパターンだけで検査する（手元の検査は局所ファイルで強くなる）", file=sys.stderr)
    if path.exists():
        extra = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return BANNED + extra


def load(root: Path):
    ledger = yaml.safe_load((root / "sros2/ledger/ledger.yaml").read_text(encoding="utf-8"))
    expected = yaml.safe_load((root / "sros2/ledger/expected-results.yaml").read_text(encoding="utf-8"))
    return ledger, expected


def check(root: Path) -> list:
    errors = []
    ledger, expected = load(root)
    defects = ledger.get("defects", [])
    ids = [d.get("id") for d in defects]
    verify = (root / "scripts/verify-env.sh").read_text(encoding="utf-8") if (root / "scripts/verify-env.sh").exists() else ""

    if len(ids) != len(set(ids)):
        errors.append("ID が重複している")
    for d in defects:
        did = d.get("id", "?")
        for f in REQUIRED:
            if not d.get(f):
                errors.append(f"{did}: 必須欄 {f} が無い")
        if not ID_RE.match(str(did)):
            errors.append(f"{did}: ID の形式が違う（B-AU-01 など）")
        if d.get("expected_verdict") not in VERDICTS:
            errors.append(f"{did}: expected_verdict が不正")
        if d.get("observability") not in OBSERVABILITY:
            errors.append(f"{did}: observability が不正")
        if d.get("behavior_impact") not in IMPACTS:
            errors.append(f"{did}: behavior_impact が不正")
        ev = d.get("evidence") or []
        if len(ev) < 1:
            errors.append(f"{did}: 根拠が 1 件も無い")
        for e in ev:
            if e.get("grade") not in GRADES or not e.get("ref"):
                errors.append(f"{did}: 根拠の grade / ref が不正")
        for c in d.get("coupled_with", []) or []:
            if c not in ids:
                errors.append(f"{did}: coupled_with の {c} が台帳に無い")
        probe = d.get("ground_truth_probe", "")
        if probe and not re.search(rf"^{re.escape(probe)}\(\)", verify, re.M):
            errors.append(f"{did}: ground_truth_probe {probe} が scripts/verify-env.sh に無い")

    # expected-results との整合
    checks = expected.get("checks", {})
    b = expected["environments"]["b"]["results"]
    for name in checks:
        for env in ("a", "b", "c"):
            if name not in expected["environments"][env]["results"]:
                errors.append(f"expected-results: 環境 {env} に {name} が無い")
    for name in checks:
        c_res = expected["environments"]["c"]["results"].get(name)
        if c_res != "PASS":
            errors.append(f"expected-results: 環境 c の {name} は PASS でなければならない")
        a_res = expected["environments"]["a"]["results"].get(name)
        if a_res not in ("FAIL", "N/A"):
            errors.append(f"expected-results: 環境 a の {name} は FAIL か N/A でなければならない")
    for d in defects:
        r = b.get(d["check"])
        verdict = r.get("verdict") if isinstance(r, dict) else r
        because = r.get("because", []) if isinstance(r, dict) else []
        if verdict != "FAIL" or d["id"] not in because:
            errors.append(f"{d['id']}: expected-results の環境 b の {d['check']} が FAIL かつ because に {d['id']} を含むこと")
    for name, r in b.items():
        for i in (r.get("because", []) if isinstance(r, dict) else []):
            if i not in ids:
                errors.append(f"expected-results: because の {i} が台帳に無い")

    # 公開してよい内容か
    targets = [root / "sros2/ledger/ledger.yaml", root / "sros2/ledger/expected-results.yaml"]
    targets += sorted((root / "docs/sros2").glob("*.md"))
    for t in targets:
        text = t.read_text(encoding="utf-8")
        for pat in banned_patterns(root):
            m = re.search(pat, text)
            if m:
                errors.append(f"{t.relative_to(root)}: 公開してはいけない語 {m.group(0)!r} が含まれる")

    # 人が読む版が台帳と一致しているか
    doc = root / "docs/sros2/defect-ledger.md"
    if doc.exists() and doc.read_text(encoding="utf-8") != render(root):
        errors.append("docs/sros2/defect-ledger.md が台帳と一致しない（`bash scripts/sros2/check-ledger.sh --render` で再生成する）")
    return errors


def render(root: Path) -> str:
    ledger, expected = load(root)
    out = []
    out.append("# 環境 B 仕込み不備台帳\n")
    out.append("<!-- このファイルは sros2/ledger/ledger.yaml から生成される。手で編集しないこと。")
    out.append("     再生成: bash scripts/sros2/check-ledger.sh --render -->\n")
    out.append(f"- 版: **{ledger['version']}**（draft の間は項目を追加・削除できる。frozen にしたら分母を動かさない）")
    out.append(f"- DDS 実装: {ledger['dds_version']}")
    out.append("- 注入: `scripts/sros2/lib/gen-keystore-inner.sh`（`setup_b`）。注入できているかは `bash scripts/verify-env.sh b` が確認する")
    out.append("- 根拠の grade: **A** = 一次情報（ソース・実測）で確認済み / **B** = 公開資料の記述（要約で確認。原典は未精読） / **C** = 要追加\n")
    out.append("| ID | 不備 | 注入の方法 | 観点（check） | 観測性 | 影響 | 連動 |")
    out.append("|---|---|---|---|---|---|---|")
    for d in ledger["defects"]:
        out.append("| {id} | {title} | {inj} | `{check}` | {obs} | {imp} | {cp} |".format(
            id=d["id"], title=d["title"], inj=d["injected_by"], check=d["check"],
            obs=d["observability"], imp=d["behavior_impact"], cp=", ".join(d.get("coupled_with", []) or []) or "-"))
    out.append("\n## 根拠\n")
    for d in ledger["defects"]:
        out.append(f"### {d['id']} {d['title']}\n")
        for e in d["evidence"]:
            out.append(f"- [{e['grade']}] {e['ref']} — {e['note']}")
        if d.get("notes"):
            out.append(f"- 注: {d['notes']}")
        out.append("")
    out.append("## 環境ごとの期待判定\n")
    out.append("| 観点（check） | 意味 | 環境 A | 環境 B | 環境 C |")
    out.append("|---|---|---|---|---|")
    envs = expected["environments"]
    for name, desc in expected["checks"].items():
        cells = []
        for env in ("a", "b", "c"):
            r = envs[env]["results"][name]
            cells.append((r["verdict"] + " (" + ", ".join(r["because"]) + ")") if isinstance(r, dict) else r)
        out.append(f"| `{name}` | {desc} | {cells[0]} | {cells[1]} | {cells[2]} |")
    out.append("")
    out.append("## 台帳の凍結と追加の運用\n")
    out.append("- 台帳は `version: draft` の間だけ項目を追加・削除できる。")
    out.append("- 検出精度の分母として使い始めるときに `sros2/ledger/ledger.yaml` の `version` を `frozen` にする。凍結後は項目を動かさない。")
    out.append("- 凍結後に追加したい不備は、別の台帳（v1.1）として管理し、凍結した台帳の分母は変えない。")
    out.append("- 連動する不備（`coupled_with`）を分母で 1 件と数えるか 2 件と数えるかは、凍結する前に決めておく。")
    out.append("- ホストの検査が必要な不備（`observability: host`）を分母に含めるかも、凍結する前に決めておく。")
    out.append("")
    return "\n".join(out)


def list_probes(root: Path) -> str:
    ledger, _ = load(root)
    return "".join(f"{d['id']}\t{d['ground_truth_probe']}\t{d['title']}\n" for d in ledger["defects"])


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("check", "render", "list"):
        print(__doc__)
        return 2
    root = Path(sys.argv[2])
    if sys.argv[1] == "render":
        sys.stdout.write(render(root))
        return 0
    if sys.argv[1] == "list":
        sys.stdout.write(list_probes(root))
        return 0
    errors = check(root)
    for e in errors:
        print("NG:", e)
    if errors:
        return 1
    print("OK: ledger check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
