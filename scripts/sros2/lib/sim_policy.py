#!/usr/bin/env python3
"""稼働中の ROS 2 グラフから作った sros2 のポリシー（`ros2 security generate_policy` の出力）を、
コンテナ 1 つぶんの enclave（sros2/policy/lab-c.xml の <enclave>）にまとめる。

ros2arm（Gazebo / MoveIt / RViz / 仮想カメラ）と ros2server（VLA ノード・変換ノード）は 1 コンテナに多数のノードがあり、
ROS_SECURITY_ENCLAVE_OVERRIDE で 1 つの enclave を共有する。permissions は enclave 単位の和集合になるので、
ノードごとのプロファイルは 1 つにまとめてよい。

- 実在するトピック・サービス名はそのまま列挙する（ワイルドカードなし）。
- 起動のたびに名前が変わるノード（moveit_<数字>、transform_listener_impl_<ハッシュ> など）の `~/` 付きサービスは、
  標準のパラメータ・ログ系サービスだけ `*/<サービス名>` のパターンにする。ノード名ごとの列挙は次の起動で外れるため。
  標準以外のサービスを持つ匿名ノードがあれば、黙って落とさずエラーにする。

使い方（ros2arm でシミュを起動したまま、同じ DDS ドメインで）:
  ros2 security generate_policy live-graph.xml
  # sros2/policy/lab-c.xml の ros2arm / ros2server は、コミット済みの入力（sros2/policy/sim-inputs/）から次のコマンドで作ってある。
  # ros2_poc_sim/test/test_sim_policy.py がこの再生成の結果とコミット済みの lab-c.xml の一致を検査する。
  D=sros2/policy/sim-inputs
  python3 scripts/sros2/lib/sim_policy.py $D/live-graph.xml --enclave /lab/ros2arm --exclude '^(vla_|_ros2cli_)' \\
      --self-clients --extra-file $D/denials.txt
  python3 scripts/sros2/lib/sim_policy.py $D/live-graph.xml --enclave /lab/ros2server --include '^(vla_|transform_listener_impl)' \\
      --extra subscribe:/camera/color/image_raw --extra publish:/vla/action --extra subscribe:/vla/ack \\
      --std-node vla_node --anon-std --extra-file $D/denials.txt
出力の <enclave> を sros2/policy/lab-c.xml に入れる。

反復: 稼働中のグラフには短命のプロセス（controller の spawner）や起動時だけ作られるクライアントが載らない。
そのポリシーで環境 c を起動し、Fast DDS の拒否ログ（"topic not found in allow rule"）を --denials に渡して
足りない分を足し、拒否が無くなるまで繰り返す。足した分は sim-inputs/denials.txt に残す（--extra-file）。
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

# どのノードにもある標準のサービス（パラメータ・ログ・型記述）。匿名ノードではこれだけをパターンにする
STD_SERVICES = (
    'describe_parameters', 'get_parameter_types', 'get_parameters', 'list_parameters', 'set_parameters',
    'set_parameters_atomically', 'get_logger_levels', 'set_logger_levels', 'get_type_description',
)
# 起動のたびに名前が変わるノード: 末尾が数字 or 16 進のハッシュ
ANON_RE = re.compile(r'(_\d{5,}|_[0-9a-f]{8,})$')
NAME_RE = re.compile(r'^[A-Za-z0-9_~/.*-]+$')


def parse(path, include, exclude):
    """(topics{publish,subscribe}, services{reply,request}, 匿名ノードの標準以外のサービス) を返す。"""
    topics = {'publish': set(), 'subscribe': set()}
    services = {'reply': set(), 'request': set()}
    anon_services = []
    seen = []
    for prof in ET.parse(path).getroot().iter('profile'):
        node, ns = prof.get('node'), prof.get('ns', '/')
        if include and not re.search(include, node):
            continue
        if exclude and re.search(exclude, node):
            continue
        seen.append(node)
        anon = bool(ANON_RE.search(node))
        for group in prof:
            for attr, kind in (('publish', topics), ('subscribe', topics), ('reply', services), ('request', services)):
                if group.get(attr) != 'ALLOW':
                    continue
                for item in group:
                    name = (item.text or '').strip()
                    if not NAME_RE.match(name):
                        sys.exit(f'想定外の名前: {name!r}（ノード {node}）')
                    if name.startswith('~/'):
                        # ~/ はそのノードの名前空間とノード名に展開する（まとめた 1 つのプロファイルでは意味が変わるため）
                        suffix = name[2:]
                        if anon:
                            if group.tag == 'services' and suffix in STD_SERVICES:
                                services[attr].add('*/' + suffix)
                            else:
                                anon_services.append((node, name))
                            continue
                        name = f'{ns.rstrip("/")}/{node}/{suffix}'
                    (topics if group.tag == 'topics' else services)[attr].add(name)
    return topics, services, anon_services, seen


ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
DENIAL_RE = re.compile(r'creation of local (reader|writer)[^(]*\((r[tqr])/(\S+) topic not found in allow rule')


def denials(path):
    """Fast DDS のログから「allow rule に無い」と拒否されたエンドポイントを (種別, 名前) のリストにする。

    稼働中のグラフには、短命のプロセス（controller の spawner など）や、起動時だけ作られるクライアントが載らない。
    ポリシーで動かしたときの拒否ログから不足分を足して、動くまで繰り返す。
    種別は publish / subscribe（トピック）と reply / request（サービス。アクションの内部サービスも同じ）。
    """
    found = set()
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = DENIAL_RE.search(ANSI_RE.sub('', line))
            if not m:
                continue
            side, prefix, name = m.groups()      # side: このノードが作ろうとした reader / writer
            if prefix == 'rt':
                found.add(('subscribe' if side == 'reader' else 'publish', '/' + name))
            elif prefix == 'rq' and name.endswith('Request'):    # rq を読む = サービスの提供側、書く = 呼び出し側
                found.add(('reply' if side == 'reader' else 'request', '/' + name[:-len('Request')]))
            elif prefix == 'rr' and name.endswith('Reply'):      # rr を読む = 呼び出し側、書く = 提供側
                found.add(('request' if side == 'reader' else 'reply', '/' + name[:-len('Reply')]))
    return sorted(found)


def render(enclave, topics, services, extras, std_nodes=(), anon_std=False, self_clients=False):
    for spec in extras:
        verb, _, name = spec.partition(':')
        if verb not in ('publish', 'subscribe', 'reply', 'request') or not NAME_RE.match(name):
            sys.exit(f'--extra は publish|subscribe|reply|request:<名前>: {spec!r}')
        (topics if verb in ('publish', 'subscribe') else services)[verb].add(name)
    if self_clients:    # 提供するサービスは、同じコンテナの別プロセス（controller の spawner、CLI）からも呼ばれる
        services['request'].update(s for s in services['reply'] if not s.startswith('*/'))
    if anon_std:    # 名前が変わる匿名ノード（ros2 CLI の _ros2cli_<数字> など）の標準サービス
        services['reply'].update('*/' + s for s in STD_SERVICES)
    for node in std_nodes:    # live に無いノード（例: 起動していない VLA ノード）の標準サービス
        if not re.match(r'^[A-Za-z0-9_]+$', node):
            sys.exit(f'--std-node はノード名: {node!r}')
        services['reply'].update(f'/{node}/{s}' for s in STD_SERVICES)
    out = [f'    <enclave path="{escape(enclave)}">', '      <profiles>', '        <profile ns="/" node="sim">']

    def block(tag, attr, names):
        if not names:
            return
        out.append(f'          <{tag} {attr}="ALLOW">')
        # 先頭の / の有無だけの違いで二重にならないよう、正規化してから並べる
        out.extend(f'            <{tag[:-1]}>{escape(n)}</{tag[:-1]}>' for n in sorted(names))
        out.append(f'          </{tag}>')

    block('topics', 'publish', topics['publish'])
    block('topics', 'subscribe', topics['subscribe'])
    block('services', 'reply', services['reply'])
    block('services', 'request', services['request'])
    out += ['        </profile>', '      </profiles>', '    </enclave>']
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('live_policy')
    ap.add_argument('--enclave', required=True, help='例: /lab/ros2arm')
    ap.add_argument('--include', help='含めるノード名の正規表現')
    ap.add_argument('--exclude', help='除くノード名の正規表現')
    ap.add_argument('--extra', action='append', default=[], help='live に無いものを足す（publish|subscribe:<トピック名> / reply|request:<サービス名>）')
    ap.add_argument('--extra-file', action='append', default=[], help='--extra を 1 行 1 件で書いたファイル（# 以降はコメント）。拒否ログから集めた不足分を残すのに使う')
    ap.add_argument('--denials', action='append', default=[], help='拒否ログ（Fast DDS の "topic not found in allow rule"）から不足分を足す')
    ap.add_argument('--self-clients', action='store_true', help='このコンテナが提供するサービスを、このコンテナも呼べるようにする（短命のクライアントはグラフに載らないため）')
    ap.add_argument('--anon-std', action='store_true', help='匿名ノード（ros2 CLI など）の標準サービスを */<名前> のパターンで足す')
    ap.add_argument('--std-node', action='append', default=[], help='live に無いノードの標準サービス（パラメータ・ログ系）を足す')
    a = ap.parse_args(argv)
    topics, services, anon, seen = parse(a.live_policy, a.include, a.exclude)
    if not seen:
        sys.exit('対象のノードが無い（--include / --exclude を確認）')
    if anon:
        sys.exit('標準のサービス以外（独自のサービス・トピック）を持つ匿名ノードがある（名前が変わるので列挙できない）: ' + ', '.join(f'{n}:{s}' for n, s in anon))
    extras = list(a.extra) + [f'{v}:{n}' for path in a.denials for v, n in denials(path)]
    for path in a.extra_file:
        with open(path, encoding='utf-8') as f:
            extras += [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith('#')]
    print(render(a.enclave, topics, services, extras, a.std_node, a.anon_std, a.self_clients))
    print(f'対象ノード {len(seen)} 個: {", ".join(sorted(seen))}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
