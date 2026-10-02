"""設定ディレクトリの解決（インストール後は share、ソースツリーでは親ディレクトリ）。"""
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
