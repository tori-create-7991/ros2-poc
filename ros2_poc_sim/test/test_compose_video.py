from ros2_poc_sim import compose_video as C

NARR = {'title': '基本動作の確認',
        'steps': [{'now': '姿勢 A へ動かす（7 関節）', 'next': 'グリッパを閉じる',
                   'command': '[ros2lab-a] $ ros2 topic pub /x positions: [1, 2] @3s'},
                  {'now': 'グリッパを閉じる', 'next': '終了（判定を表示します）',
                   'command': '[ros2arm] $ ros2 action send_goal /g'}]}


def _results():
    return [
        {'index': 0, 'name': 'pose_a', 'verdict': 'PASS', 'joint_err': 0.01, 'changed_ratio': 0.031,
         'bbox': (100, 50, 300, 400), 'ee_px': (200.0, 240.0), 'reasons': [],
         't_start': 105.0, 't_sent': 106.0, 't_end': 110.0},
        {'index': 1, 'name': 'grip', 'verdict': 'FAIL', 'joint_err': 0.5, 'changed_ratio': 0.0,
         'bbox': None, 'ee_px': None, 'reasons': ['関節誤差 0.500 rad > 許容 0.100'], 'codes': ['joint_err 0.500>0.100'],
         't_start': 112.0, 't_sent': 113.0, 't_end': 114.0},
    ]


def _build(**kw):
    args = dict(results=_results(), narration=NARR, t0=100.0, cam_t0=100.0, cam_h=480)
    args.update(kw)
    return C.build(**args)


def _dialogues(ass):
    return [line for line in ass.splitlines() if line.startswith('Dialogue:')]


def test_build_stacks_desktop_and_camera_with_bar():
    g, _ = _build(cam_t0=101.5)
    assert g.startswith('[0:v]fps=10,scale=-2:720,setsar=1[d];[1:v]tpad=start_duration=1.500')
    assert ('[d][c]hstack=inputs=2,tpad=stop_mode=clone:stop_duration=3,pad=iw:ih+170:0:0:color=black,'
            'ass=overlay/steps.ass[out]') in g


def test_camera_started_before_desktop_is_trimmed():
    g, _ = _build(cam_t0=99.0)
    assert '[1:v]trim=start=1.000,setpts=PTS-STARTPTS' in g


def test_boxes_are_relative_and_scaled():
    g, _ = _build()
    # 480 → 720 で 1.5 倍。判定の時刻（t_end）から次のステップの開始まで表示する
    assert "drawbox=x=150:y=75:w=300:h=525:color=0x33dd55@0.9:t=4:enable='between(t,10.000,12.000)'" in g
    assert 'drawbox=x=293:y=353:w=14:h=14:color=yellow' in g
    assert 'drawtext' not in g


def test_japanese_text_goes_to_ass_file_not_into_filtergraph():
    g, texts = _build()
    assert set(texts) == {'overlay/steps.ass'}
    assert '姿勢' not in g and 'trajectory' not in g
    ass = texts['overlay/steps.ass']
    assert 'PlayResY: 890' in ass and 'Noto Sans CJK JP' in ass
    assert 'ステップ 1/2  いま: 姿勢 A へ動かす（7 関節）' in ass
    assert 'つぎ: グリッパを閉じる' in ass and 'つぎ: 終了（判定を表示します）' in ass
    assert '$ ros2 topic pub /x positions: [1, 2] @3s' in ass


def test_dialogue_windows_follow_the_step_times():
    _, texts = _build()
    d = _dialogues(texts['overlay/steps.ass'])
    # ステップ 1: 5.00〜12.00 秒に「いま」、判定待ちが 5.00〜10.00、判定が 10.00〜12.00
    assert any('0:00:05.00,0:00:12.00,Now,,0,0,0,,ステップ 1/2' in x for x in d)
    assert any('0:00:05.00,0:00:10.00,Pending' in x and '動作の完了を待っています' in x for x in d)
    assert any('0:00:10.00,0:00:12.00,Pass' in x and '合格 — 関節の誤差 0.010 rad・映像の変化 3.1%' in x for x in d)
    # 最後のステップは t_end + TAIL_SEC まで。不合格は理由が日本語で出て、総合結果が右下に出る
    assert any('0:00:14.00,0:00:17.00,Fail' in x and '不合格 — 関節の誤差が大きい' in x for x in d)
    assert any('0:00:14.00,0:00:17.00,SumFail,,0,0,0,,総合 1/2 合格' in x for x in d)


def test_title_is_shown_before_the_first_step():
    _, texts = _build()
    d = _dialogues(texts['overlay/steps.ass'])
    assert any('0:00:00.00,0:00:05.00,Now,,0,0,0,,基本動作の確認' in x for x in d)
    assert any('0:00:00.00,0:00:05.00,Next' in x and 'つぎ: 姿勢 A へ動かす' in x for x in d)
    _, texts = _build(t0=104.8)     # 最初のステップがほぼ録画の先頭ならタイトルは出さない
    assert '基本動作の確認' not in texts['overlay/steps.ass']


def test_failed_send_shows_reason_without_t_end():
    res = [{'index': 0, 'name': 'a', 'verdict': 'FAIL', 'reasons': ['命令が送れていない（rc=1）'],
            'codes': ['send_failed rc=1'], 't_start': 101.0, 't_sent': 102.0}]
    _, texts = _build(results=res, narration={'steps': NARR['steps'][:1]})
    ass = texts['overlay/steps.ass']
    assert '不合格 — 指令を送れなかった（rc=1）' in ass and '総合' not in ass


def test_special_characters_cannot_make_ass_tags_or_lines():
    evil = {'title': '', 'steps': [{'now': '{\\an9\\fs99}x\\Ny\nz', 'next': '}', 'command': 'a\rb'}]}
    _, texts = _build(results=_results()[:1], narration=evil)
    for line in _dialogues(texts['overlay/steps.ass']):
        text = line.split(',,0,0,0,,', 1)[1]
        assert '{' not in text and '}' not in text and '\\' not in text
    # 改行コードを含む入力でも、字幕の行数（Dialogue の数）は増えない
    assert all(x.startswith(('Dialogue:', '[', 'Format:', 'Style:', 'ScriptType', 'PlayRes', 'WrapStyle',
                             'Scaled')) or not x for x in texts['overlay/steps.ass'].splitlines())


def test_camera_only_when_no_desktop():
    g, _ = _build(has_desktop=False)
    assert g.startswith('[0:v]tpad') and '[c]null,tpad=stop_mode=clone' in g and 'hstack' not in g
    assert C.ffmpeg_args(g, has_desktop=False)[4:6] == ['-i', 'camera.mp4']


def test_missing_narration_lines_fall_back_to_the_step_name():
    _, texts = _build(narration={})
    assert 'いま: pose_a' in texts['overlay/steps.ass']


def test_ass_timestamps():
    assert C._ts(0) == '0:00:00.00' and C._ts(65.456) == '0:01:05.46' and C._ts(-3) == '0:00:00.00'
    assert C._ts(3723.5) == '1:02:03.50'
