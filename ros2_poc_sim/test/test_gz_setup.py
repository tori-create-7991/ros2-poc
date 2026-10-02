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
    def __init__(self, models, service=True, add_ok=True, spawn_ok=None):
        self.models, self.service, self.add_ok = models, service, add_ok
        self.spawn_ok = spawn_ok or {}
        self.added = 0
        self.spawned = []

    def install(self, monkeypatch):
        monkeypatch.setattr(Z, 'wait_for_service', lambda w, t: self.service)
        monkeypatch.setattr(Z, 'list_models', lambda w: self.models)
        monkeypatch.setattr(Z, 'add_sensors_system', self._add)
        monkeypatch.setattr(Z, 'spawn', self._spawn)
        monkeypatch.setattr(Z.time, 'sleep', lambda s: None)
        return self

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


def test_run_wraps_timeout_as_rc124(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd='gz', timeout=1, output=b'partial')
    monkeypatch.setattr(Z.subprocess, 'run', boom)
    r = Z._run(['gz', 'x'], timeout=1)
    assert r.returncode == 124 and 'timeout' in r.stderr


def test_parse_model_names_ignores_non_model_blocks():
    text = 'light {\n  name: "sun"\n}\nmodel {\n  name: "A"\n}\nmodel {\n}\nlight {\n  name: "L2"\n}\n'
    assert Z.parse_model_names(text) == {'A'}
