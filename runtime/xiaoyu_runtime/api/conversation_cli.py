"""The same authenticated directory API for CLI, Codex and Claude Code readers."""
import json
import time
import urllib.parse
import urllib.request


def add_arguments(commands):
    parser = commands.add_parser('conversations', help='统一查看和管理各端会话目录，不启动模型')
    parser.add_argument('action', choices=('list', 'show', 'history', 'rename', 'pin', 'unpin', 'archive', 'restore', 'project', 'metadata', 'open'))
    parser.add_argument('--id', help='统一目录中的 uid')
    parser.add_argument('--title')
    parser.add_argument('--project')
    parser.add_argument('--cursor')
    parser.add_argument('--query', default='')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--include-archived', action='store_true')
    parser.add_argument('--sources', action='store_true', help='也显示已关联任务的原生来源')


def call(config, path, body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://127.0.0.1:%s%s' % (config.port, path),
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + config.token, 'Content-Type': 'application/json'})
    with opener.open(request, timeout=15) as response:
        return json.load(response)


def settle(config, result, seconds=15):
    deadline = time.monotonic() + seconds
    command, authority = result.get('command_id'), result.get('authority')
    while result.get('status') == 'queued' and command and authority and time.monotonic() < deadline:
        time.sleep(.25)
        result = call(config, '/v1/directory/operations/%s?authority=%s' % (urllib.parse.quote(command), urllib.parse.quote(authority)))
    return result


def directory(config, query=''):
    rows, cursor = [], None
    while True:
        result = call(config, '/v1/directory?' + urllib.parse.urlencode({'q':query, **({'cursor':cursor} if cursor else {})}))
        rows.extend(result['entries'])
        cursor = result.get('cursor')
        if not result.get('more') or not cursor:
            return rows


def run_cli(config, args):
    try:
        rows = directory(config, args.query)
        if args.action == 'list':
            result = [row for row in rows if (args.include_archived or not row['archived'])
                      and (args.sources or not (row['kind'] == 'native' and row.get('bound')))]
        else:
            row = next((row for row in rows if row['uid'] == args.id), None)
            if row is None:
                raise ValueError('请先用 conversations list 选择确切记录；不会按同名或目录猜关联')
            path = '/v1/directory/' + urllib.parse.quote(row['uid'])
            if args.action == 'show':
                result = row
            elif args.action == 'open':
                result = call(config, path + '/open', {})
            elif args.action == 'metadata':
                result = call(config, path + '/metadata', {})
            elif args.action == 'history':
                result = settle(config, call(config, path + '/history' + ('?cursor=' + urllib.parse.quote(args.cursor) if args.cursor else '')))
                if result.get('history'):
                    result = result['history']
            else:
                fields = {'rename': {'title': args.title}, 'project': {'project': args.project},
                          'pin': {'pinned': True}, 'unpin': {'pinned': False},
                          'archive': {'archived': True}, 'restore': {'archived': False}}[args.action]
                result = settle(config, call(config, path, {'fields': fields, 'revision': row['metadata_revision']}))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif isinstance(result, list):
            for row in result:
                print('%s\t%s\t%s\t%s\t%s' % (row['uid'], row['kind'], row['title'], row.get('source_name', ''), row.get('project', '')))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if isinstance(result, dict) and result.get('status') == 'failed' else 0
    except (OSError, ValueError, KeyError) as error:
        print('统一会话目录暂不可用：%s' % error)
        return 2
