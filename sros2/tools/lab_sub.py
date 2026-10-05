"""検証用: 指定トピックを一定時間 subscribe し、受信数と見えた publisher 数を表示する。
使い方: docker exec -i <container> bash -lc 'python3 - <topic> <seconds> [<node_name>]' < sros2/tools/lab_sub.py
出力: `RECEIVED <件数> PUBLISHERS_SEEN <最大の publisher 数>`
SROS2 の Enforce で起動できるよう rosout / parameter services / type description service を切ってある。
"""
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

topic = sys.argv[1]
seconds = float(sys.argv[2])
name = sys.argv[3] if len(sys.argv) > 3 else "lab_sub"

rclpy.init(args=["--ros-args", "-p", "start_type_description_service:=false"])
node = Node(name, enable_rosout=False, start_parameter_services=False)
count = [0]
node.create_subscription(String, topic, lambda _m: count.__setitem__(0, count[0] + 1), 10)
seen = 0
end = time.time() + seconds
while time.time() < end:
    rclpy.spin_once(node, timeout_sec=0.2)
    seen = max(seen, node.count_publishers(topic))
print("RECEIVED", count[0], "PUBLISHERS_SEEN", seen, flush=True)
