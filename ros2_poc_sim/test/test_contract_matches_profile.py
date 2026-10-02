"""契約ファイル（手書き）とプロファイル YAML が食い違っていないこと（実機に流す前提のドリフト検出）。"""
from pathlib import Path

import yaml

from ros2_poc_sim import profile as P

ROOT = Path(__file__).resolve().parents[1] / 'config'


def _contract(name):
    return yaml.safe_load((ROOT / 'contracts' / f'{name}.yaml').read_text(encoding='utf-8'))


def test_each_profile_has_a_matching_contract():
    for path in sorted((ROOT / 'profiles').glob('*.yaml')):
        prof = P.load_profile(path.stem)
        contract = _contract(path.stem)
        expected = {t['name']: t for t in contract['topics'] + contract.get('optional_topics', [])}
        for sid, st in prof['topics']['streams'].items():
            topic = P.topic_name(prof, sid)
            assert topic in expected, f'{path.stem}: {topic} が契約に無い'
            e = expected[topic]
            assert e['type'] == st['type'], topic
            if 'encoding' in st:
                assert e.get('encoding') == st['encoding'], topic
            assert e['frame_id'] == prof['frames'][st['frame']], topic


def test_contract_camera_info_matches_intrinsics():
    for path in sorted((ROOT / 'profiles').glob('*.yaml')):
        prof = P.load_profile(path.stem)
        for name, exp in (_contract(path.stem).get('camera_info') or {}).items():
            assert exp['width'] == prof['sensor']['width']
            assert exp['height'] == prof['sensor']['height']
            assert abs(exp['fx'] - prof['intrinsics']['fx']) < 1e-6, name


def test_no_two_profiles_share_a_frame_name():
    seen = {}
    for path in sorted((ROOT / 'profiles').glob('*.yaml')):
        for real in P.load_profile(path.stem)['frames'].values():
            assert real not in seen, f'{real} を {seen.get(real)} と {path.stem} が共有（TF が衝突する）'
            seen[real] = path.stem
