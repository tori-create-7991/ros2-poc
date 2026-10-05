from ros2_poc_sim import compose_video as C

CMDS = [('ros2lab-a', ['$ ros2 topic pub --once -w 1 /x trajectory_msgs/msg/JointTrajectory',
                       '    positions: [1, 2] @3s']),
        ('ros2arm', ['$ ros2 action send_goal /g control_msgs/action/ParallelGripperCommand', '    j -> 0 rad'])]


def _results():
    return [
        {'index': 0, 'name': 'pose_a', 'verdict': 'PASS', 'joint_err': 0.01, 'changed_ratio': 0.031,
         'bbox': (100, 50, 300, 400), 'ee_px': (200.0, 240.0), 'reasons': [],
         't_start': 105.0, 't_sent': 106.0, 't_end': 110.0},
        {'index': 1, 'name': 'grip', 'verdict': 'FAIL', 'joint_err': 0.5, 'changed_ratio': 0.0,
         'bbox': None, 'ee_px': None, 'reasons': ['関節誤差 0.500 rad > 許容 0.100'], 'codes': ['joint_err 0.500>0.100'],
         't_start': 112.0, 't_sent': 113.0, 't_end': 114.0},
    ]


def test_build_stacks_desktop_and_camera_with_bar():
    g, _ = C.build(results=_results(), commands=CMDS, t0=100.0, cam_t0=101.5, cam_h=480)
    assert g.startswith('[0:v]fps=10,scale=-2:720,setsar=1[d];[1:v]tpad=start_duration=1.500')
    assert '[d][c]hstack=inputs=2,pad=iw:ih+170:0:0:color=black' in g and g.endswith('[out]')


def test_camera_started_before_desktop_is_trimmed():
    g, _ = C.build(results=_results(), commands=CMDS, t0=100.0, cam_t0=99.0, cam_h=480)
    assert '[1:v]trim=start=1.000,setpts=PTS-STARTPTS' in g


def test_time_windows_and_boxes_are_relative_and_scaled():
    g, _ = C.build(results=_results(), commands=CMDS, t0=100.0, cam_t0=100.0, cam_h=480)
    assert "textfile=overlay/step000_cmd.txt" in g and "enable='between(t,5.000,12.000)'" in g
    assert "textfile=overlay/step000_verdict.txt" in g and "enable='between(t,10.000,12.000)'" in g
    # 480 → 720 で 1.5 倍
    assert 'drawbox=x=150:y=75:w=300:h=525:color=0x33dd55@0.9' in g
    assert 'drawbox=x=293:y=353:w=14:h=14:color=yellow' in g
    # 最後のステップは t_end + TAIL_SEC まで表示し、総合結果を出す
    assert "enable='between(t,14.000,17.000)'" in g and "enable='gte(t,14.000)'" in g


def test_texts_go_to_files_not_into_filtergraph():
    g, texts = C.build(results=_results(), commands=CMDS, t0=100.0, cam_t0=100.0, cam_h=480)
    assert 'trajectory_msgs' not in g and 'send_goal' not in g and 'expansion=none' in g
    assert texts['overlay/step000_cmd.txt'] == ('STEP 1/2  pose_a  [ros2lab-a]\n'
                                                '$ ros2 topic pub --once -w 1 /x trajectory_msgs/msg/JointTrajectory\n'
                                                '    positions: [1, 2] @3s')
    assert texts['overlay/step000_verdict.txt'] == 'STEP 1 pose_a: PASS (joint err 0.010 rad, change 3.1%)'
    assert texts['overlay/step001_verdict.txt'] == 'STEP 2 grip: FAIL - joint_err 0.500>0.100'
    assert texts['overlay/summary.txt'] == 'RESULT 1/2 PASS'


def test_failed_send_shows_reason_without_t_end():
    res = [{'index': 0, 'name': 'a', 'verdict': 'FAIL', 'reasons': ['命令が送れていない（rc=1）'], 'codes': ['send_failed rc=1'],
            't_start': 101.0, 't_sent': 102.0}]
    g, texts = C.build(results=res, commands=CMDS[:1], t0=100.0, cam_t0=100.0, cam_h=480)
    assert 'rc=1' in texts['overlay/step000_verdict.txt'] and 'summary' not in ''.join(texts)


def test_camera_only_when_no_desktop():
    g, _ = C.build(results=_results(), commands=CMDS, t0=100.0, cam_t0=100.0, cam_h=480, has_desktop=False)
    assert g.startswith('[0:v]tpad') and '[c]null,pad=' in g and 'hstack' not in g
    assert C.ffmpeg_args(g, has_desktop=False)[4:6] == ['-i', 'camera.mp4']


def test_wrap_lines_truncates():
    lines = C.wrap_lines('x' * 1000, width=50, max_lines=3)
    assert len(lines) == 3 and lines[-1].endswith('...') and all(len(x) <= 50 for x in lines)


def test_overlay_texts_are_ascii():
    _, texts = C.build(results=_results(), commands=CMDS, t0=100.0, cam_t0=100.0, cam_h=480)
    assert all(t.isascii() for t in texts.values())
