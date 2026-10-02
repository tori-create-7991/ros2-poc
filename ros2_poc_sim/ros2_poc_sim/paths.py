"""設定ディレクトリの解決と、生成物・ロックの置き場（安全なディレクトリ）。"""
import os
import re
import stat
from pathlib import Path


def package_root() -> Path:
    here = Path(__file__).resolve().parent
    src_root = here.parent
    if (src_root / 'config').is_dir():
        return src_root
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory('ros2_poc_sim'))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError('ros2_poc_sim の設定ディレクトリが見つからない') from exc


def config_dir(kind: str) -> Path:
    return package_root() / 'config' / kind


def _check_dir(d: str) -> None:
    st = os.lstat(d)
    if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f'{d} がシンボリックリンクか他人の所有: 作り直してください')
    os.chmod(d, 0o700)


def private_dir(name: str = '', base: str = None) -> str:
    """生成物（SDF・ブリッジ YAML）とロックの置き場。他ユーザーに差し替えられないよう
    0700・実体ディレクトリ・自分の所有に限る（launch と gz_setup が同じ場所を使う）。
    base を検査してから name を作る（シンボリックリンク越しに作らない）。"""
    if name and not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise ValueError(f'name は英数字とアンダースコアのみ: {name!r}')
    base = base or f'/tmp/ros2_poc_sim_{os.getuid()}'
    try:
        os.mkdir(base, 0o700)
    except FileExistsError:
        pass
    _check_dir(base)
    if not name:
        return base
    path = os.path.join(base, name)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    _check_dir(path)
    return path
