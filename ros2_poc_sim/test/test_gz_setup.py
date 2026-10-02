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
