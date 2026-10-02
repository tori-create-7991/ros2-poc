"""verify_sim_camera.sh の期待値が config/placements と食い違わないことを検査する。"""
import re
from pathlib import Path

import pytest

from ros2_poc_sim import profile as P

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_sim_camera.sh'


def test_expected_object_position_matches_placement():
    text = SCRIPT.read_text(encoding='utf-8')
    m = re.search(r'EXPECT="\$\{EXPECT:-([-\d. ]+)\}"', text)
    assert m, 'EXPECT の既定値が見つからない'
    x, y, z = (float(v) for v in m.group(1).split())
    obj = P.load_placement('fixed_near_top')['object']['xyz_in_base_link']
    assert (x, y) == pytest.approx((obj[0], obj[1]))
    assert z == pytest.approx(0.02)       # 立方体の半辺（机上面が base_link の z=0）


def test_v1_judged_by_exit_code_not_substring():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'PYTEST_RC' in text
    assert '*passed*)' not in text         # "1 failed, 69 passed" を ok にしてしまう判定は使わない
    assert 'ROS_DOMAIN_ID=97' in text      # 稼働中のドメインに混ざらない


def test_script_never_runs_arm_moving_examples():   # pick_and_place_tf は検出のみ（実行はしない）
    text = SCRIPT.read_text(encoding='utf-8')
    code = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
    assert 'camera_example.launch' not in code and 'ros2 run crane_x7_examples pick' not in code


def _tool():
    import shutil
    return shutil.which('sha1sum') or shutil.which('shasum')


def _run_with_fake_docker(tmp_path, pytest_out, mutate=None, profile='usb_cam', node_list=None,
                          host_mutate=None):
    """PATH 先頭の偽 docker で verify_sim_camera.sh を隔離環境で実行し、出力を返す。
    イメージ内のソースは手元のコピー（mutate で 1 ファイルだけ変えれば「古いイメージ」）。"""
    import os
    import shutil
    import stat
    import subprocess
    repo = SCRIPT.parents[2]
    tree = tmp_path / 'image_tree'
    shutil.copytree(repo / 'ros2_poc_sim', tree, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))   # .dockerignore と同じ
    # ホスト側は別のコピー（リポジトリ本体は書き換えない。イメージ内で読み取り専用でも動く）。スクリプトもそのコピーを実行する
    host_root = tmp_path / 'host'
    shutil.copytree(repo / 'ros2_poc_sim', host_root / 'ros2_poc_sim',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if host_mutate:
        host_mutate(host_root / 'ros2_poc_sim')
    if mutate:
        mutate(tree)
    out_file = tmp_path / 'pytest_out.txt'
    out_file.write_text(pytest_out + '\n')
    calls = tmp_path / 'calls.log'
    fake = tmp_path / 'docker'
    nodes_cmd = f'cat "{tmp_path / "nodes.txt"}"; exit 0' if node_list is not None else 'exit 1'
    if node_list is not None:
        (tmp_path / 'nodes.txt').write_text(node_list)
    fake.write_text(f"""#!/usr/bin/env bash
echo "$*" >> "{calls}"
case "$1" in
  ps) echo cid; exit 0;;
  cp) exit 0;;
  exec)
    all="$*"
    if echo "$all" | grep -q 'sha1sum'; then
      cd "{tree}" && find ros2_poc_sim test config launch scripts resource setup.py setup.cfg package.xml -type f ! -name '*.pyc' ! -path '*__pycache__*' -print0 | LC_ALL=C sort -z | xargs -0 {_tool()}
      exit 0
    fi
    if echo "$all" | grep -q 'pytest'; then cat "{out_file}"; exit 0; fi
    if echo "$all" | grep -q 'node list'; then {nodes_cmd}; fi
    exit 1;;
  *) echo "unexpected docker $*" >&2; exit 99;;
esac
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {'PATH': f'{tmp_path}:/usr/bin:/bin', 'HOME': str(tmp_path), 'ARM': 'fakearm', 'LAB': 'fakelab',
           'OUT_DIR': str(tmp_path / 'out')}
    script = host_root / 'ros2_poc_sim' / 'scripts' / 'verify_sim_camera.sh'
    r = subprocess.run(['bash', str(script), profile], capture_output=True, text=True, env=env,
                       cwd=str(host_root), timeout=120)
    return r.stdout, calls.read_text() if calls.exists() else ''


def _line(out, key):
    return next((l for l in out.splitlines() if key in l), '')


needs_tool = pytest.mark.skipif(__import__('shutil').which('sha1sum') is None
                                and __import__('shutil').which('shasum') is None,
                                reason='sha1sum / shasum が無い')


@needs_tool
def test_v1_fails_on_nonzero_rc_even_if_output_says_passed(tmp_path):
    out, _ = _run_with_fake_docker(tmp_path, '=== 1 failed, 69 passed ===\nPYTEST_RC=1')
    assert _line(out, 'V1-pytest').startswith('FAIL'), out


@needs_tool
def test_v1_ok_on_rc0(tmp_path):
    out, _ = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0')
    assert _line(out, 'V1-pytest').startswith('ok'), out


@needs_tool
def test_v1_detects_a_one_file_difference_and_names_it(tmp_path):
    def mutate(tree):
        f = tree / 'ros2_poc_sim' / 'profile.py'
        f.write_text(f.read_text() + '\n# changed\n')
    out, _ = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0', mutate=mutate)
    line = _line(out, 'V1-pytest')
    assert line.startswith('FAIL') and '古い' in line and 'profile.py' in line, out


@needs_tool
def test_v1_detects_a_package_metadata_difference(tmp_path):
    def mutate(tree):
        (tree / 'package.xml').write_text((tree / 'package.xml').read_text() + '<!-- x -->')
    out, _ = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0', mutate=mutate)
    assert _line(out, 'V1-pytest').startswith('FAIL') and 'package.xml' in _line(out, 'V1-pytest'), out


@needs_tool
def test_v7b_is_aborted_when_pick_and_place_tf_is_running_and_stub_never_runs(tmp_path):
    out, calls = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0', profile='realsense_d435',
                                       node_list='/camera_adapter\n/pick_and_place_tf\n')
    assert _line(out, 'V7b').startswith('FAIL') and 'pick_and_place_tf' in _line(out, 'V7b'), out
    assert 'vla_stub_detect.py' not in ' '.join(l for l in calls.splitlines() if 'timeout' in l or ' python3 ' in l)


@needs_tool
def test_v7b_fails_closed_when_node_list_is_unavailable(tmp_path):
    out, calls = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0', profile='realsense_d435',
                                       node_list=None)
    assert _line(out, 'V7b').startswith('FAIL') and '中止' in _line(out, 'V7b'), out
    assert not any('python3 /tmp/vla_stub_detect.py' in l for l in calls.splitlines())


def test_script_guards_against_arm_moving_node():
    assert 'pick_and_place_tf' in SCRIPT.read_text(encoding='utf-8')


def test_host_file_list_excludes_what_dockerignore_excludes():
    """イメージに入らないファイルがホスト側の一覧に残ると、再ビルドしても直らない「古い」誤判定になる。"""
    text = SCRIPT.read_text(encoding='utf-8')
    di = SCRIPT.parents[2] / '.dockerignore'
    if not di.exists():   # イメージ内のコピー（.dockerignore は COPY されない）では検査できない
        pytest.skip('.dockerignore が無い')
    ignore = di.read_text(encoding='utf-8').split()
    assert '**/.DS_Store' in ignore and '**/__pycache__' in ignore
    assert '.DS_Store' in text and '__pycache__' in text and '*.pyc' in text


@needs_tool
def test_dot_ds_store_on_host_does_not_make_the_image_look_stale(tmp_path):
    def junk(tree):
        (tree / 'test' / '.DS_Store').write_bytes(b'x')
        (tree / 'config' / '.DS_Store').write_bytes(b'y')
    out, _ = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0', host_mutate=junk)
    assert _line(out, 'V1-pytest').startswith('ok'), out


def test_script_guards_against_arm_moving_node():
    assert 'pick_and_place_tf' in SCRIPT.read_text(encoding='utf-8')


def test_host_file_list_excludes_what_dockerignore_excludes():
    """イメージに入らないファイルがホスト側の一覧に残ると、再ビルドしても直らない「古い」誤判定になる。"""
    text = SCRIPT.read_text(encoding='utf-8')
    di = SCRIPT.parents[2] / '.dockerignore'
    if not di.exists():   # イメージ内のコピー（.dockerignore は COPY されない）では検査できない
        pytest.skip('.dockerignore が無い')
    ignore = di.read_text(encoding='utf-8').split()
    assert '**/.DS_Store' in ignore and '**/__pycache__' in ignore
    assert '.DS_Store' in text and '__pycache__' in text and '*.pyc' in text


@needs_tool
def test_dot_ds_store_on_host_does_not_make_the_image_look_stale(tmp_path):
    import shutil
    repo = SCRIPT.parents[2]
    junk = repo / 'ros2_poc_sim' / 'test' / '.DS_Store'
    existed = junk.exists()
    junk.write_bytes(b'x')
    try:
        out, _ = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0')
    finally:
        if not existed:
            junk.unlink()
    assert _line(out, 'V1-pytest').startswith('ok'), out
