"""VLA ノードの制御ループ（ROS 非依存）。1 ステップずつ「画像 → VLA → /vla/action → ack 待ち」を回す。

実時間周期ではなく 1 ステップずつ完了を待つ（シミュは RTF が低く、OpenVLA の 5 Hz には届かないため）。
終了コード: 0 = 全ステップ ok、1 = どれかのステップが ok 以外（ik_failed / rejected / timeout）、
2 = 環境の問題（画像が来ない・VLA サーバーに繋がらない・応答が契約外・ack が来ない）。
"""
import time

from ros2_poc_sim import vla_action as A
from ros2_poc_sim import vla_client as V

EXIT_OK, EXIT_STEP_FAILED, EXIT_ENV = 0, 1, 2
MAX_STEPS = 100


def run_loop(steps, get_image, query, publish, wait_ack, log=print, start_seq=1,
             on_step=None, clock=time.time):
    """steps 回のステップを実行する。(終了コード, 各ステップの結果のリスト) を返す。

    get_image() -> 画像 | None、query(画像) -> 7 次元（VlaError を送出しうる）、publish(Action)、
    wait_ack(seq) -> (seq, status, reason[, detail]) | None。
    on_step(info) は ack を受けたステップごとに呼ぶ（記録用）。info は index / seq / vector（VLA の 7 次元）/
    action / status / reason / detail / t_start（指令を出す直前の時刻）。
    """
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS:
        raise ValueError(f'steps は 1〜{MAX_STEPS} の整数（{steps!r}）')
    results = []
    for i in range(steps):
        seq = start_seq + i
        image = get_image()
        if image is None:
            log(f'step {i + 1}/{steps}: カメラ画像が来ない')
            return EXIT_ENV, results
        try:
            vector = query(image)
            action = A.action_from_vector(seq, vector)
        except (V.VlaError, ValueError) as e:
            log(f'step {i + 1}/{steps}: VLA の取得に失敗: {e}')
            return EXIT_ENV, results
        t_start = clock()
        publish(action)
        ack = wait_ack(seq)
        if ack is None or ack[0] != seq:
            log(f'step {i + 1}/{steps}: seq={seq} の ack が来ない（変換ノードが起動しているか確認する）')
            return EXIT_ENV, results
        _seq, status, reason, *rest = ack
        detail = rest[0] if rest else None
        results.append({'seq': seq, 'action': action, 'status': status, 'reason': reason})
        if on_step is not None:
            on_step({'index': i, 'seq': seq, 'vector': [float(v) for v in vector], 'action': action,
                     'status': status, 'reason': reason, 'detail': detail, 't_start': t_start})
        log(f'step {i + 1}/{steps}: seq={seq} delta={tuple(round(v, 4) for v in action.delta)} '
            f'gripper={action.gripper:.2f} → {status}{" （" + reason + "）" if reason else ""}')
        if status != 'ok':
            return EXIT_STEP_FAILED, results
    log(f'完了: {steps}/{steps} ステップが ok')
    return EXIT_OK, results
