"""設定ディレクトリの解決と、生成物・ロックの置き場（安全なディレクトリ）。"""
import os
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


def private_dir(name: str = '', base: str = None) -> str:
    """生成物（SDF・ブリッジ YAML）とロックの置き場。他ユーザーに差し替えられないよう
    0700・実体ディレクトリ・自分の所有に限る（launch と gz_setup が同じ場所を使う）。"""
    base = base or f'/tmp/ros2_poc_sim_{os.getuid()}'
    os.makedirs(base, mode=0o700, exist_ok=True)
    path = os.path.join(base, name) if name else base
    if name:
        os.makedirs(path, mode=0o700, exist_ok=True)
    for d in {base, path}:
        st = os.lstat(d)
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f'{d} がシンボリックリンクか他人の所有: 作り直してください')
        os.chmod(d, 0o700)
    return path
