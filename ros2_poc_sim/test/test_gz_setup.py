from ros2_poc_sim import gz_setup as Z

SCENE = '''name: "default"
model {
  name: "ground_plane"
  id: 8
  link {
    name: "link"
    visual {
      name: "visual"
    }
  }
}
model {
  name: "Table"
  link {
    name: "link"
  }
}
model {
  name: "sim_camera_realsense_d435"
  link {
    name: "link"
    sensor { name: "sensor" }
  }
}
light {
  name: "sun"
}
'''


def test_parse_model_names_only_top_level_models():
    assert Z.parse_model_names(SCENE) == {'ground_plane', 'Table', 'sim_camera_realsense_d435'}


def test_parse_model_names_empty():
    assert Z.parse_model_names('') == set()


def test_needs_sensors_system_only_when_no_sim_camera_model():
    assert Z.needs_sensors_system({'ground_plane', 'Table'}) is True
    assert Z.needs_sensors_system({'ground_plane', 'sim_camera_usb_cam'}) is False


# ---- run_setup の分岐（gz CLI をモンキーパッチ）-------------------------------
import argparse  # noqa: E402

import pytest  # noqa: E402

from ros2_poc_sim import sdf as S  # noqa: E402


def _args(**kw):
    base = dict(camera_sdf='/tmp/c.sdf', camera_name='sim_camera_x', camera_pose=[0] * 6,
                object_sdf='/tmp/o.sdf', object_name='blue_cube', object_pose=[0] * 6,
                world='default', world_entity_id=1, wait_sec=1.0, spawn_timeout=1.0)
    base.update(kw)
    return argparse.Namespace(**base)


class Fake:
    def __init__(self, models, service=True, add_ok=True, spawn_ok=None, marker_visible=True):
        self.models, self.service, self.add_ok = models, service, add_ok
        self.marker_visible = marker_visible
        self.spawn_ok = spawn_ok or {}
        self.added = 0
        self.spawned = []

    def install(self, monkeypatch):
        monkeypatch.setattr(Z, 'wait_for_service', lambda w, t: (self.service, 'last-error'))
        monkeypatch.setattr(Z, 'list_models', self._list)
        monkeypatch.setattr(Z, 'list_models_retry', lambda w, b: self._list(w))
        monkeypatch.setattr(Z, 'wait_for_model', lambda w, n, b: self.marker_visible)
        monkeypatch.setattr(Z, 'add_sensors_system', self._add)
        monkeypatch.setattr(Z, 'spawn', self._spawn)
        monkeypatch.setattr(Z, '_write_temp_sdf', lambda text: __import__('os').devnull)
        monkeypatch.setattr(Z.os, 'unlink', lambda p: None)
        monkeypatch.setattr(Z.time, 'sleep', lambda s: None)
        return self

    def _list(self, w):
        return self.models

    def _add(self, w, i):
        self.added += 1
        return self.add_ok

    def _spawn(self, world, name, path, pose, timeout):
        self.spawned.append(name)
        return self.spawn_ok.get(name, True)


def test_first_run_adds_sensors_marker_camera_object(monkeypatch):
    f = Fake({'ground_plane'}).install(monkeypatch)
    assert Z.run_setup(_args()) == 0
    assert f.added == 1
    assert f.spawned == [S.SENSORS_MARKER_NAME, 'sim_camera_x', 'blue_cube']   # マーカーがカメラより先


def test_rerun_with_everything_present_does_nothing(monkeypatch):
    f = Fake({'ground_plane', S.SENSORS_MARKER_NAME, 'sim_camera_x', 'blue_cube'}).install(monkeypatch)
    assert Z.run_setup(_args()) == 0
    assert f.added == 0 and f.spawned == []


def test_second_profile_does_not_readd_sensors(monkeypatch):
    f = Fake({'ground_plane', S.SENSORS_MARKER_NAME, 'sim_camera_realsense_d435', 'blue_cube'}).install(monkeypatch)
    assert Z.run_setup(_args(camera_name='sim_camera_usb_cam')) == 0
    assert f.added == 0 and f.spawned == ['sim_camera_usb_cam']


def test_camera_spawn_failure_then_rerun_is_safe(monkeypatch):
    f = Fake({'ground_plane'}, spawn_ok={'sim_camera_x': False}).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_CAMERA
    assert f.added == 1 and S.SENSORS_MARKER_NAME in f.spawned
    # 次の実行: Gazebo の状態にはマーカーだけがある → Sensors は再追加しない
    f2 = Fake({'ground_plane', S.SENSORS_MARKER_NAME}).install(monkeypatch)
    assert Z.run_setup(_args()) == 0
    assert f2.added == 0


def test_world_missing(monkeypatch):
    Fake(set(), service=False).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_NO_WORLD


def test_unknown_state_aborts_without_guessing(monkeypatch):
    f = Fake(None).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_UNKNOWN_STATE
    assert f.added == 0 and f.spawned == []


def test_sensors_add_failure(monkeypatch):
    f = Fake({'ground_plane'}, add_ok=False).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_SENSORS
    assert f.spawned == []


def test_marker_spawn_failure_reports(monkeypatch):
    Fake({'ground_plane'}, spawn_ok={S.SENSORS_MARKER_NAME: False}).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_SENSORS


def test_object_spawn_failure(monkeypatch):
    Fake({'ground_plane'}, spawn_ok={'blue_cube': False}).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_OBJECT


def test_run_wraps_timeout_as_rc124_and_kills_group():
    r = Z._run(['sleep', '30'], timeout=1)
    assert r.returncode == 124 and 'timeout' in r.stderr


def test_run_reports_missing_binary_as_127():
    r = Z._run(['definitely-not-a-command-xyz'], timeout=1)
    assert r.returncode == 127 and 'definitely-not-a-command-xyz' in r.stderr


def test_parse_model_names_ignores_non_model_blocks():
    text = 'light {\n  name: "sun"\n}\nmodel {\n  name: "A"\n}\nmodel {\n}\nlight {\n  name: "L2"\n}\n'
    assert Z.parse_model_names(text) == {'A'}


def test_marker_not_visible_aborts_so_next_run_is_not_blind(monkeypatch):
    f = Fake({'ground_plane'}, marker_visible=False).install(monkeypatch)
    assert Z.run_setup(_args()) == Z.EXIT_SENSORS
    assert 'sim_camera_x' not in f.spawned          # カメラへ進まない


def test_marker_spawn_is_retried_once(monkeypatch):
    f = Fake({'ground_plane'}).install(monkeypatch)
    results = iter([False, True])
    monkeypatch.setattr(Z, 'spawn', lambda w, n, p, pose, t: (f.spawned.append(n), next(results, True))[1])
    assert Z.run_setup(_args(object_sdf='')) == 0
    assert f.spawned.count(S.SENSORS_MARKER_NAME) == 2


# ---- gz CLI を _run 差し替えで通す（I/O ヘルパ自体の検査）--------------------------
def _cp(rc=0, out='', err=''):
    import subprocess
    return subprocess.CompletedProcess([], rc, out, err)


def test_list_models_failure_is_none_not_empty(monkeypatch):
    for r in (_cp(124, '', 'timeout'), _cp(0, '', ''), _cp(1, 'model {', '')):
        monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60, _r=r: _r)
        assert Z.list_models('default') is None


def test_list_models_parses_success(monkeypatch):
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: _cp(0, 'model {\n  name: "A"\n}\n'))
    assert Z.list_models('default') == {'A'}


def test_add_sensors_system_requires_data_true(monkeypatch):
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: _cp(0, 'data: true\n'))
    assert Z.add_sensors_system('default', 1) is True
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: _cp(0, 'data: false\n'))
    assert Z.add_sensors_system('default', 1) is False
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: _cp(124, 'data: true', 'timeout'))
    assert Z.add_sensors_system('default', 1) is False


def test_wait_for_service_timeout_returns_false_with_reason(monkeypatch):
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: _cp(1, '', 'gz: not found'))
    monkeypatch.setattr(Z.time, 'sleep', lambda s: None)
    ticks = iter(range(0, 1000, 5))
    monkeypatch.setattr(Z.time, 'time', lambda: next(ticks))
    ok, last = Z.wait_for_service('default', 20)
    assert ok is False and 'not found' in last


def test_wait_for_service_found(monkeypatch):
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: _cp(0, '/world/default/entity/system/add\n'))
    assert Z.wait_for_service('default', 5)[0] is True


def test_spawn_argv_has_pose_flags_and_no_rename(monkeypatch):
    seen = {}
    monkeypatch.setattr(Z, '_run', lambda cmd, timeout=60: (seen.update(cmd=cmd), _cp(0))[1])
    assert Z.spawn('default', 'cam', '/x.sdf', S.pose_args((1.0, 2.0, 3.0, 0, 0, 0.5)), 10)
    cmd = seen['cmd']
    assert cmd[:4] == ['ros2', 'run', 'ros_gz_sim', 'create']
    assert cmd[cmd.index('-allow_renaming') + 1] == 'false'
    assert [cmd[cmd.index(f) + 1] for f in ('-x', '-y', '-z', '-Y')] == ['1', '2', '3', '0.5']


def test_pose_argument_accepts_negative_exponents_python312():
    a = Z.parse_args(['--camera-sdf', 'c', '--camera-name', 'n',
                      '--camera-pose=0,0,1.4,-1.2246467991473532e-16,-1e-05,0.0'])
    assert a.camera_pose[3] == pytest.approx(-1.2246467991473532e-16) and a.camera_pose[4] == -1e-05


def test_pose_roundtrip():
    pose = [0.2, -0.0, 1.435, -1e-05, 1e-16, 3.14]
    assert Z._pose(Z.format_pose(pose)) == pose


def test_object_sdf_requires_pose_and_name():
    with pytest.raises(SystemExit):
        Z.parse_args(['--camera-sdf', 'c', '--camera-name', 'n', '--camera-pose=0,0,0,0,0,0',
                      '--object-sdf', 'o.sdf'])


def test_world_name_is_validated():
    with pytest.raises(SystemExit):
        Z.parse_args(['--camera-sdf', 'c', '--camera-name', 'n', '--camera-pose=0,0,0,0,0,0',
                      '--world', '../x'])


def test_lock_times_out_when_held(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(Z.paths, 'private_dir', lambda name='', base=None: str(tmp_path))
    held = open(tmp_path / 'gz_setup_default.lock', 'a')
    fcntl.flock(held, fcntl.LOCK_EX)
    monkeypatch.setattr(Z.time, 'sleep', lambda s: None)
    ticks = iter(range(0, 10000, 3))
    monkeypatch.setattr(Z.time, 'time', lambda: next(ticks))
    assert Z.acquire_lock('default', 10) is None
    held.close()
