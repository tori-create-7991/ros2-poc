"""launch ファイルの煙テスト。_setup が組み立てる gz_setup のコマンドが、gz_setup 自身の argparse で
そのまま解釈できること（引数の食い違いが実機でしか見つからない、を防ぐ）。launch が無ければ skip。"""
import importlib.util
import os
import stat
from pathlib import Path

import pytest

pytest.importorskip('launch')
pytest.importorskip('launch_ros')

from launch import LaunchContext  # noqa: E402
from launch.actions import ExecuteProcess  # noqa: E402

from ros2_poc_sim import gz_setup as Z  # noqa: E402
from ros2_poc_sim import paths  # noqa: E402

LAUNCH = Path(__file__).resolve().parents[1] / 'launch'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, LAUNCH / f'{name}.launch.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _text(x):
    """launch の Substitution（またはその列）を文字列にする（実行前なので perform せず text を連結）。"""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return ''.join(_text(i) for i in x)
    return getattr(x, 'text', str(x))


def _node_name(action):
    return _text(getattr(action, '_Node__node_name', ''))


def _cmd(proc):
    return [_text(c) for c in proc.cmd]


def _context(mod, **overrides):
    ctx = LaunchContext()
    for k, v in {**mod.LAUNCH_ARGS, **overrides}.items():
        ctx.launch_configurations[k] = v
    return ctx


def test_sim_camera_declares_all_arguments():
    mod = _load('sim_camera')
    ld = mod.generate_launch_description()
    declared = {a.name for a in ld.entities if a.__class__.__name__ == 'DeclareLaunchArgument'}
    assert declared == set(mod.LAUNCH_ARGS)
    assert {'world', 'wait_sec', 'spawn_timeout', 'pointcloud', 'compressed', 'fail_fast'} <= declared


def test_arm_with_camera_declares_exactly_the_same_arguments():
    ac = _load('arm_with_camera')
    sc = _load('sim_camera')
    pytest.importorskip('ament_index_python')
    try:
        ld = ac.generate_launch_description()
    except Exception as exc:  # crane_x7_gazebo / ros2_poc_sim が未インストールの環境
        pytest.skip(f'ament 環境が無い: {exc}')
    declared = {a.name for a in ld.entities if a.__class__.__name__ == 'DeclareLaunchArgument'}
    assert declared == set(sc.LAUNCH_ARGS)


@pytest.mark.parametrize('profile,placement', [
    ('realsense_d435', 'fixed_near_top'), ('usb_cam', 'fixed_front_oblique'),
])
def test_gz_setup_command_is_parsed_by_gz_setup(tmp_path, monkeypatch, profile, placement):
    mod = _load('sim_camera')
    monkeypatch.setattr(paths, 'private_dir',
                        lambda name='', base=None: str(tmp_path / (name or 'base')))
    (tmp_path / profile).mkdir(exist_ok=True)
    actions = mod._setup(_context(mod, profile=profile, placement=placement))
    proc = next(a for a in actions if isinstance(a, ExecuteProcess))
    cmd = _cmd(proc)
    assert cmd[1:3] == ['-m', 'ros2_poc_sim.gz_setup']
    args = Z.parse_args(cmd[3:])
    assert args.camera_name == f'sim_camera_{profile}' and len(args.camera_pose) == 6
    assert len(args.object_pose) == 6 and args.object_name == 'blue_cube'
    assert os.path.exists(args.camera_sdf) and os.path.exists(args.object_sdf)


def test_spawn_object_false_omits_object_arguments(tmp_path, monkeypatch):
    mod = _load('sim_camera')
    monkeypatch.setattr(paths, 'private_dir', lambda name='', base=None: str(tmp_path))
    actions = mod._setup(_context(mod, spawn_object='false'))
    proc = next(a for a in actions if isinstance(a, ExecuteProcess))
    cmd = _cmd(proc)
    assert not any(c.startswith('--object') for c in cmd)
    Z.parse_args(cmd[3:])


def test_node_names_are_per_profile(tmp_path, monkeypatch):
    mod = _load('sim_camera')
    monkeypatch.setattr(paths, 'private_dir', lambda name='', base=None: str(tmp_path))
    actions = mod._setup(_context(mod, profile='usb_cam', placement='fixed_front_oblique'))
    names = {_node_name(a) for a in actions if hasattr(a, '_Node__node_name')}
    assert 'sim_camera_bridge_usb_cam' in names and 'camera_adapter_usb_cam' in names
    assert 'rep_usb_cam_color_image' in names


def test_world_name_and_ns_mode_are_validated(tmp_path, monkeypatch):
    mod = _load('sim_camera')
    monkeypatch.setattr(paths, 'private_dir', lambda name='', base=None: str(tmp_path))
    with pytest.raises(RuntimeError):
        mod._setup(_context(mod, world='../x'))
    with pytest.raises(Exception):
        mod._setup(_context(mod, ns_mode='Nested'))


def test_adapter_receives_user_supplied_placement_value(tmp_path, monkeypatch):
    import yaml
    from ros2_poc_sim import profile as P
    mod = _load('sim_camera')
    monkeypatch.setattr(paths, 'private_dir', lambda name='', base=None: str(tmp_path))
    d = P.load_placement('fixed_near_top')
    d['name'] = 'my_custom'
    f = tmp_path / 'custom.yaml'
    f.write_text(yaml.safe_dump(d))
    actions = mod._setup(_context(mod, placement=str(f)))
    adapter = next(a for a in actions if _node_name(a) == 'camera_adapter_realsense_d435')
    flat = {}
    for p in adapter._Node__parameters:
        if isinstance(p, dict):
            for k, v in p.items():
                flat[_text(k)] = _text(v) if not isinstance(v, (bool, int, float)) else v
    assert flat['placement'].split('\n')[0] == str(f)        # 名前ではなく、ユーザーが渡した値（パス）をそのまま渡す


def test_failure_handler_does_not_shutdown_by_default(tmp_path, monkeypatch):
    from launch.actions import EmitEvent, RegisterEventHandler
    mod = _load('sim_camera')
    monkeypatch.setattr(paths, 'private_dir', lambda name='', base=None: str(tmp_path))

    class Ev:
        returncode = 2

    for fail_fast, expect_shutdown in (('false', False), ('true', True)):
        actions = mod._setup(_context(mod, fail_fast=fail_fast))
        handler = next(a for a in actions if isinstance(a, RegisterEventHandler))
        produced = handler.event_handler._OnActionEventBase__on_event \
            if hasattr(handler.event_handler, '_OnActionEventBase__on_event') else None
        assert produced is not None
        out = produced(Ev(), None)
        has_shutdown = any(isinstance(x, EmitEvent) for x in out)
        assert has_shutdown is expect_shutdown
        ok = produced(type('Ev0', (), {'returncode': 0})(), None)
        assert ok == []


def test_private_dir_is_0700(tmp_path):
    d = paths.private_dir('p', base=str(tmp_path / 'base'))
    assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(os.path.dirname(d)).st_mode) == 0o700


def test_private_dir_rejects_symlinked_base(tmp_path):
    target = tmp_path / 'elsewhere'
    target.mkdir()
    link = tmp_path / 'base'
    os.symlink(target, link)
    with pytest.raises(RuntimeError):
        paths.private_dir('p', base=str(link))


def test_private_dir_without_name_returns_base(tmp_path):
    base = str(tmp_path / 'b')
    assert paths.private_dir(base=base) == base
