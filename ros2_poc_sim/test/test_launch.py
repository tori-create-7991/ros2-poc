"""launch ファイルの煙テスト（引数の宣言・既定値・ラッパの引数同期）。launch が無ければ skip。"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('launch')
pytest.importorskip('launch_ros')

LAUNCH = Path(__file__).resolve().parents[1] / 'launch'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, LAUNCH / f'{name}.launch.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sim_camera_declares_all_arguments():
    mod = _load('sim_camera')
    ld = mod.generate_launch_description()
    declared = {a.name for a in ld.entities if a.__class__.__name__ == 'DeclareLaunchArgument'}
    assert declared == set(mod.LAUNCH_ARGS)
    assert {'world', 'wait_sec', 'spawn_timeout', 'pointcloud', 'compressed'} <= declared


def test_private_dir_is_0700(tmp_path):
    import os
    import stat
    mod = _load('sim_camera')
    d = mod._private_dir('p', base=str(tmp_path / 'base'))
    assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(os.path.dirname(d)).st_mode) == 0o700


def test_private_dir_rejects_symlinked_base(tmp_path):
    import os
    mod = _load('sim_camera')
    target = tmp_path / 'elsewhere'
    target.mkdir()
    link = tmp_path / 'base'
    os.symlink(target, link)
    with pytest.raises(RuntimeError):
        mod._private_dir('p', base=str(link))
