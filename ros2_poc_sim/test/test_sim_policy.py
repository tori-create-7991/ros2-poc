"""scripts/sros2/lib/sim_policy.py（稼働中のグラフと拒否ログから enclave のポリシーを作る）のテスト。"""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('sim_policy', ROOT / 'scripts' / 'sros2' / 'lib' / 'sim_policy.py')
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

LIVE = '''<policy version="0.2.0"><enclaves><enclave path="/">
<profiles>
  <profile node="crane_x7_arm_controller" ns="/">
    <services reply="ALLOW"><service>~/list_parameters</service><service>~/query_state</service></services>
    <topics subscribe="ALLOW"><topic>~/joint_trajectory</topic><topic>tf</topic></topics>
    <topics publish="ALLOW"><topic>~/controller_state</topic><topic>/rosout</topic></topics>
  </profile>
  <profile node="moveit_587508927" ns="/">
    <services reply="ALLOW"><service>~/get_parameters</service><service>~/list_parameters</service></services>
    <topics publish="ALLOW"><topic>/rosout</topic></topics>
  </profile>
  <profile node="transform_listener_impl_acc9af12c910" ns="/">
    <services reply="ALLOW"><service>~/get_type_description</service></services>
    <topics subscribe="ALLOW"><topic>tf_static</topic></topics>
  </profile>
  <profile node="vla_converter" ns="/">
    <topics publish="ALLOW"><topic>/vla/ack</topic></topics>
    <services request="ALLOW"><service>compute_ik</service></services>
  </profile>
  <profile node="_ros2cli_12345" ns="/">
    <topics publish="ALLOW"><topic>/rosout</topic></topics>
  </profile>
</profiles></enclave></enclaves></policy>'''


@pytest.fixture
def live(tmp_path):
    p = tmp_path / 'live.xml'
    p.write_text(LIVE, encoding='utf-8')
    return p


def _items(xml, tag):
    return sorted(e.text for e in ET.fromstring(f'<r>{xml}</r>').iter(tag))


def test_stable_node_relative_names_are_expanded_with_the_node_name(live):
    topics, services, anon, seen = P.parse(live, None, r'^(vla_|_ros2cli_)')
    assert '/crane_x7_arm_controller/joint_trajectory' in topics['subscribe']
    assert '/crane_x7_arm_controller/controller_state' in topics['publish']
    assert '/crane_x7_arm_controller/query_state' in services['reply']
    assert not any(n.startswith('~') for n in topics['publish'] | topics['subscribe'] | services['reply'])
    assert anon == []
    assert 'vla_converter' not in seen


def test_anonymous_nodes_only_get_standard_service_patterns(live):
    _topics, services, anon, _seen = P.parse(live, None, r'^(vla_|_ros2cli_)')
    assert '*/get_parameters' in services['reply'] and '*/get_type_description' in services['reply']
    assert not any('moveit_587508927' in s for s in services['reply'])    # 起動のたびに変わる名前は列挙しない
    assert anon == []


def test_anonymous_node_with_a_custom_service_is_an_error(tmp_path):
    p = tmp_path / 'live.xml'
    p.write_text('<policy><enclaves><enclave path="/"><profiles><profile node="foo_12345678" ns="/">'
                 '<services reply="ALLOW"><service>~/do_something</service></services></profile></profiles></enclave></enclaves></policy>')
    _t, _s, anon, _seen = P.parse(p, None, None)
    assert anon == [('foo_12345678', '~/do_something')]
    with pytest.raises(SystemExit):
        P.main([str(p), '--enclave', '/lab/x'])


def test_include_and_exclude_select_nodes(live):
    _t, _s, _a, seen = P.parse(live, r'^vla_', None)
    assert seen == ['vla_converter']
    with pytest.raises(SystemExit):
        P.main([str(live), '--enclave', '/lab/x', '--include', '^nothing$'])


def test_render_has_no_wildcards_except_the_standard_service_patterns(live, capsys):
    P.main([str(live), '--enclave', '/lab/ros2arm', '--exclude', r'^(vla_|_ros2cli_)', '--self-clients'])
    xml = capsys.readouterr().out
    names = _items(xml, 'topic') + _items(xml, 'service')
    wild = [n for n in names if '*' in n]
    assert wild and all(n.startswith('*/') and n[2:] in P.STD_SERVICES for n in wild)
    assert '/crane_x7_arm_controller/query_state' in _items(xml, 'service')


def test_self_clients_adds_request_for_every_served_service_except_patterns(live, capsys):
    P.main([str(live), '--enclave', '/lab/ros2arm', '--exclude', r'^(vla_|_ros2cli_)', '--self-clients'])
    root = ET.fromstring(capsys.readouterr().out)
    req = {s.text for g in root.iter('services') if g.get('request') == 'ALLOW' for s in g}
    assert '/crane_x7_arm_controller/query_state' in req
    assert not any(s.startswith('*/') for s in req)


def test_extras_std_node_and_anon_std(live, capsys):
    P.main([str(live), '--enclave', '/lab/ros2server', '--include', '^vla_', '--extra', 'publish:/vla/action',
            '--extra', 'subscribe:/vla/ack', '--extra', 'request:/compute_ik', '--std-node', 'vla_node', '--anon-std'])
    xml = capsys.readouterr().out
    assert '/vla/action' in _items(xml, 'topic') and '/vla_node/get_parameters' in _items(xml, 'service')
    assert '*/get_parameters' in _items(xml, 'service')
    for bad in ('publish:/x y', 'bogus:/x', 'publish'):
        with pytest.raises(SystemExit):
            P.render('/lab/x', {'publish': set(), 'subscribe': set()}, {'reply': set(), 'request': set()}, [bad])


def test_denials_are_converted_to_policy_entries(tmp_path):
    log = tmp_path / 'sim.log'
    log.write_text('\x1b[31m[SECURITY Error] Error checking creation of local reader  (rr/controller_manager/list_controllersReply '
                   'topic not found in allow rule. (Permissions.cpp:1246))\n'
                   '[SECURITY Error] Error checking creation of local writer  (rq/x/yRequest topic not found in allow rule.\n'
                   '[SECURITY Error] Error checking creation of local reader  (rq/svc/zRequest topic not found in allow rule.\n'
                   '[SECURITY Error] Error checking creation of local writer  (rr/svc/zReply topic not found in allow rule.\n'
                   '[SECURITY Error] Error checking creation of local reader  (rt/camera/image topic not found in allow rule.\n'
                   '[SECURITY Error] Error checking creation of local writer  (rt/vla/action topic not found in allow rule.\n'
                   'unrelated line\n', encoding='utf-8')
    assert set(P.denials(log)) == {
        ('publish', '/vla/action'), ('reply', '/svc/z'), ('request', '/controller_manager/list_controllers'),
        ('request', '/x/y'), ('subscribe', '/camera/image')}


def test_the_committed_policy_is_valid_and_only_wildcards_the_standard_services():
    root = ET.parse(ROOT / 'sros2' / 'policy' / 'lab-c.xml').getroot()
    enclaves = {e.get('path'): e for e in root.iter('enclave')}
    assert {'/lab/ros2lab_a', '/lab/ros2lab_b', '/lab/ros2arm', '/lab/ros2server'} <= set(enclaves)
    for path, e in enclaves.items():
        for item in list(e.iter('topic')) + list(e.iter('service')):
            name = item.text.strip()
            assert '~' not in name, f'{path}: 展開されていない ~ がある: {name}'
            if '*' in name:
                assert path in ('/lab/ros2arm', '/lab/ros2server') and name.startswith('*/') and name[2:] in P.STD_SERVICES, \
                    f'{path}: 許可していないワイルドカード: {name}'
