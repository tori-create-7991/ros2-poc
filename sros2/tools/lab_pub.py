"""検証用: 指定トピックへ String を一定時間 publish する。
使い方: docker exec -i <container> bash -lc 'python3 - <topic> <seconds> [<node_name>]' < sros2/tools/lab_pub.py
SROS2 の Enforce で起動できるよう rosout / parameter services / type description service を切ってある
（permissions にそれらのトピックを入れていないため）。
"""
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

topic = sys.argv[1]
seconds = float(sys.argv[2])
name = sys.argv[3] if len(sys.argv) > 3 else "lab_pub"

rclpy.init(args=["--ros-args", "-p", "start_type_description_service:=false"])
node = Node(name, enable_rosout=False, start_parameter_services=False)
pub = node.create_publisher(String, topic, 10)
print("PUB_UP", flush=True)
end = time.time() + seconds
while time.time() < end:
    pub.publish(String(data="LABPAYLOAD-7F3A"))
    rclpy.spin_once(node, timeout_sec=0.2)
