"""SROS2 の環境 b / c で ros2arm / ros2server を動かす設定（compose の override と Dockerfile.arm）の静的テスト。

実コンテナでの確認は手動（docs/sros2/README.md の実測）。ここでは、設定の取り違え・消し忘れで壊れていないことを守る。
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SROS2 = yaml.safe_load((ROOT / 'docker-compose.sros2.yml').read_text(encoding='utf-8'))
BASE = yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))


def _env(service):
    return dict(e.split('=', 1) for e in SROS2['services'][service]['environment'])


def test_ros2arm_hands_over_to_the_base_entrypoint_and_gives_the_key_to_ubuntu():
    arm = SROS2['services']['ros2arm']
    assert arm['entrypoint'] == ['/sros2/entrypoint.sh']
    assert 'command' not in arm          # ベースイメージの CMD のまま（entrypoint が /entrypoint.sh に引き継ぐ）
    env = _env('ros2arm')
    assert env['SROS2_NEXT'] == '/entrypoint.sh'
    assert env['SROS2_CHOWN'] == '1000:1000'
    assert env['SROS2_ENV_FILE'] == '/run/sros2-env.sh'
    assert env['ROS_SECURITY_ENABLE'] == 'true'
    assert any(t.startswith('/run/sros2:') and 'uid=1000' in t and 'gid=1000' in t and 'mode=0700' in t for t in arm['tmpfs'])
    # 自分の enclave だけを read-only で渡す（他コンテナの鍵・CA の秘密鍵は渡さない）
    keys = [v for v in arm['volumes'] if v.endswith(':/sros2/src:ro')]
    assert len(keys) == 1 and '/containers/ros2arm:' in keys[0]


def test_ros2server_is_wired_like_ros2lab():
    srv = SROS2['services']['ros2server']
    assert srv['entrypoint'] == ['/sros2/entrypoint.sh']
    env = _env('ros2server')
    assert env['ROS_SECURITY_ENCLAVE_OVERRIDE'].startswith('${SROS2_ENCLAVE_ROS2SERVER')
    assert 'SROS2_CHOWN' not in env      # root で動くので不要
    keys = [v for v in srv['volumes'] if v.endswith(':/sros2/src:ro')]
    assert len(keys) == 1 and '/containers/ros2server:' in keys[0]


@pytest.mark.parametrize('service', ['ros2lab-a', 'ros2lab-b', 'ros2arm', 'ros2server'])
def test_every_sros2_container_fails_closed_without_up_env(service):
    """SROS2_STRATEGY / SROS2_ENV は必須（${VAR:?}）。素の docker compose で b / c の設定が使われないようにする。"""
    text = yaml.safe_dump(SROS2['services'][service])
    assert 'SROS2_STRATEGY' in text and 'SROS2_ENV' in text
    raw = (ROOT / 'docker-compose.sros2.yml').read_text(encoding='utf-8')
    assert raw.count('${SROS2_STRATEGY:?') >= 4 and raw.count('${SROS2_ENV:?') >= 4


@pytest.mark.parametrize('service', ['ros2arm', 'ros2server'])
def test_environment_label_follows_sros2_env(service):
    assert BASE['services'][service]['labels']['ros2poc.env'] == '${SROS2_ENV:-a}'


def test_dockerfile_arm_bashrc_reads_the_sros2_env_file():
    """デスクトップの端末はコンテナの ENV を継承しない。読まないと、端末から起動したノードだけセキュリティなしで動く。"""
    text = (ROOT / 'Dockerfile.arm').read_text(encoding='utf-8')
    assert "'[ -f /run/sros2-env.sh ] && source /run/sros2-env.sh' >> /home/ubuntu/.bashrc" in text


def test_the_keystore_is_generated_for_all_four_containers():
    text = (ROOT / 'scripts' / 'sros2' / 'lib' / 'gen-keystore-inner.sh').read_text(encoding='utf-8')
    assert 'CONTAINERS="ros2lab-a ros2lab-b ros2arm ros2server"' in text
