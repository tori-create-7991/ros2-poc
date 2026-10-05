import yaml

from ros2_poc_sim import bridge_config as B
from ros2_poc_sim import profile as P


def _entries(name, **kw):
    prof = P.load_profile(name)
    return {e['ros_topic_name']: e for e in yaml.safe_load(B.bridge_yaml(prof, **kw))}


def test_rgbd_default_has_no_points():
    e = _entries('realsense_d435')
    assert set(e) == {
        '/sim_camera/realsense_d435/raw/image',
        '/sim_camera/realsense_d435/raw/depth_image',
        '/sim_camera/realsense_d435/raw/camera_info',
    }


def test_pointcloud_adds_points():
    e = _entries('realsense_d435', pointcloud=True)
    p = e['/sim_camera/realsense_d435/raw/points']
    assert p['gz_type_name'] == 'gz.msgs.PointCloudPacked'
    assert p['ros_type_name'] == 'sensor_msgs/msg/PointCloud2'
    assert p['gz_topic_name'] == '/sim_camera/realsense_d435/points'


def test_all_lazy_and_gz_to_ros():
    for ent in _entries('realsense_d435', pointcloud=True).values():
        assert ent['lazy'] is True
        assert ent['direction'] == 'GZ_TO_ROS'


def test_camera_sensor_topics():
    e = _entries('usb_cam')
    assert set(e) == {'/sim_camera/usb_cam/raw/image', '/sim_camera/usb_cam/raw/camera_info'}
    assert e['/sim_camera/usb_cam/raw/image']['gz_topic_name'] == '/sim_camera/usb_cam/image'
    assert e['/sim_camera/usb_cam/raw/camera_info']['gz_topic_name'] == '/sim_camera/usb_cam/camera_info'
