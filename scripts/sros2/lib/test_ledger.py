"""台帳の検査（ledger.py check）が、壊れた台帳をちゃんと落とすかを確認する自己テスト（変異テスト）。

  python3 test_ledger.py <リポジトリのルート>

リポジトリの台帳・期待結果・検証スクリプト・生成物の docs を一時ディレクトリへコピーし、1 か所ずつ壊して
check が失敗する（エラーが出る）ことを確認する。壊していない状態では成功することも確認する。
検査が空振り（何を渡しても通る）になっていないことを担保するためのテスト。
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

FILES = [
    "sros2/ledger/ledger.yaml",
    "sros2/ledger/expected-results.yaml",
    "scripts/verify-env.sh",
    "docs/sros2/defect-ledger.md",
]


def make_copy(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for f in FILES:
        dst = tmp / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / f, dst)
    return tmp


def edit(root: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"自己テストの前提が崩れている: {rel} に {old!r} が無い")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


def env_replace(root: Path, env: str, old: str, new: str) -> None:
    """expected-results.yaml の環境 env（"  a:" / "  b:" / "  c:"）の区画の中だけを置換する。"""
    p = root / "sros2/ledger/expected-results.yaml"
    text = p.read_text(encoding="utf-8")
    start = text.index(f"\n  {env}:\n")
    nxt = [text.find(f"\n  {e}:\n", start + 1) for e in "abc" if e != env]
    nxt = [i for i in nxt if i > start]
    end = min(nxt) if nxt else len(text)
    section = text[start:end]
    if old not in section:
        raise SystemExit(f"自己テストの前提が崩れている: 環境 {env} に {old!r} が無い")
    p.write_text(text[:start] + section.replace(old, new, 1) + text[end:], encoding="utf-8")


MUTATIONS = [
    ("存在しない probe を指す", lambda r: edit(r, "sros2/ledger/ledger.yaml", "probe_B_AU_01", "probe_B_AU_99")),
    ("coupled_with が存在しない ID を指す", lambda r: edit(r, "sros2/ledger/ledger.yaml", "coupled_with: [B-CR-02]", "coupled_with: [B-XX-99]")),
    ("期待結果の because から ID が消える", lambda r: edit(r, "sros2/ledger/expected-results.yaml", "because: [B-AU-05]", "because: []")),
    ("環境 c の期待判定が FAIL になる", lambda r: env_replace(r, "c", "unauthenticated-join: PASS", "unauthenticated-join: FAIL")),
    ("環境 a の期待判定が PASS になる", lambda r: env_replace(r, "a", "unauthenticated-join: FAIL", "unauthenticated-join: PASS")),
    ("根拠が 1 件も無い", lambda r: edit(r, "sros2/ledger/ledger.yaml", "evidence:\n      - {grade: A, ref: \"sros2 0.13.6 sros2/_utilities.py\", note: \"証明書の有効期間が 3650 日固定（コンテナ内のソースと実測の notAfter）\"}\n", "evidence: []\n")),
    ("ID が重複する", lambda r: edit(r, "sros2/ledger/ledger.yaml", "id: B-AU-02", "id: B-AU-01")),
    ("expected_verdict が不正", lambda r: edit(r, "sros2/ledger/ledger.yaml", "expected_verdict: FAIL", "expected_verdict: MAYBE")),
    ("人が読む版が台帳と食い違う", lambda r: (r / "docs/sros2/defect-ledger.md").write_text((r / "docs/sros2/defect-ledger.md").read_text(encoding="utf-8") + "\n手で書き足した\n", encoding="utf-8")),
    ("公開してはいけない語（私的アドレス）が混ざる", lambda r: (r / "docs/sros2/defect-ledger.md").write_text((r / "docs/sros2/defect-ledger.md").read_text(encoding="utf-8") + "\n192.168.1.10\n", encoding="utf-8")),
]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    failures = []

    base = make_copy(src)
    errs = ledger.check(base)
    if errs:
        failures.append(f"壊していない台帳で check が失敗した: {errs}")
    shutil.rmtree(base)

    for name, mutate in MUTATIONS:
        root = make_copy(src)
        mutate(root)
        errs = ledger.check(root)
        shutil.rmtree(root)
        if not errs:
            failures.append(f"壊したのに check が通った: {name}")
        else:
            print(f"ok: {name} → 検出した（{len(errs)} 件）")

    for f in failures:
        print("NG:", f)
    if failures:
        return 1
    print("OK: ledger selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
