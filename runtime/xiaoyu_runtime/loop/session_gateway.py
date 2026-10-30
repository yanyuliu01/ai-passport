"""One read/selection boundary for managed tasks and owner-native sessions.

The gateway never owns transcripts, permissions or a second dispatch journal.
Selections execute through the existing task/effect ports and source adapters.
"""
import json
import re
import threading
from dataclasses import replace
from pathlib import Path

from ..errors import AgentError
from ..world.conversation_directory import agent_home, home_scope, native_agent
from ..helpers.factory import create


def _words(value):
    return re.sub(r"[\s_\-/]+", "", str(value).casefold())


class SessionGateway:
    def __init__(self, config, cards, agents, remote=lambda: None):
        self.config, self.cards, self.agents = config, cards, agents
        self.remote = remote
        self.authority = None
        self.lock = threading.RLock()

    def targets(self, conversation):
        # Native sources are explicitly owner-visible shared records. Ordinary
        # tasks from other side chats remain scoped to their original chat.
        values = {c['id']: c for c in self.cards.session_targets(conversation)}
        identities = {(c.get('mesh_origin') or self.authority, native_agent(c.get('agent')), c.get('native_scope'), c.get('session_id'))
                      for c in values.values() if c.get('monitoring_only')}
        for row in self.cards.directory.source_snapshot().values():
            identity = (row.get('authority'), native_agent(row.get('agent')), row.get('native_scope'), row.get('native_session_id'))
            if row.get('internal') or identity in identities:
                continue
            values[row['uid']] = dict(row, id=row['uid'], conversation='__desktop__',
                seq=row.get('source_revision', 0), session_id=row.get('native_session_id'),
                desktop_cwd=row.get('cwd'), monitoring_only=True, source='desktop',
                desktop_status={'working':'running', 'turn_ended':'idle'}.get(row.get('state'), row.get('state')),
                mesh_origin=row.get('authority') if row.get('authority') != self.authority else None)
        return values

    def target(self, key, conversation):
        current = self.cards.get(key) if key else None
        if current is not None:
            return current if current.get('monitoring_only') or current.get('conversation') == conversation else None
        return self.targets(conversation).get(key) if isinstance(key, str) and key.startswith('native-') else None

    def adapter(self, target, bind=False):
        """Select a protocol adapter using the original source identity, never a title."""
        if target.get('mesh_origin'):
            raise AgentError('原会话属于另一台 Runtime，必须交回来源电脑续接，不能在这里另开')
        if target.get('available') is False or target.get('source') == 'external':
            raise AgentError('原会话来源暂不可用，尚未投递；原记录保留')
        wanted = native_agent(target.get('agent'))
        candidates = [a for a in self.agents() if native_agent(a.type) == wanted and a.native_resume]
        if not candidates:
            raise AgentError('这台 Runtime 没有支持原会话类型 %s 的适配器' % wanted)
        scope = target.get('native_scope')
        if not scope:
            # Compatibility for old explicitly selected work cards. They cannot
            # be rebound to another home without an observed scope.
            return candidates[0]
        for agent in candidates:
            if home_scope(agent_home(agent.spec)) == scope:
                return agent
        # Source adapters, not arbitrary model text, establish allowed homes.
        # Existing discovery currently supports these two native protocols.
        defaults = {'codex': self.config.desktop_codex_dir or Path.home()/'.codex',
                    'claude': self.config.desktop_claude_dir or Path.home()/'.claude'}
        home = defaults.get(wanted)
        if home is None or home_scope(home) != scope:
            raise AgentError('原会话的运行环境没有匹配的适配器，尚未投递；不会改用另一个环境')
        routing = {'ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                   'OPENAI_BASE_URL', 'OPENAI_API_KEY'}
        if routing & (set(candidates[0].spec.env) | set(candidates[0].spec.env_files)):
            raise AgentError('配置的执行端使用独立提供方，不能替代原运行环境；尚未投递')
        if not bind:
            return candidates[0]
        from ..events.native_history import recent
        row = dict(agent=wanted, native_scope=scope, native_session_id=target.get('session_id'))
        if recent(self.config, row).get('coverage') == 'native-source-unavailable':
            raise AgentError('无法核实原运行环境中的会话记录，尚未投递')
        # Use the source's login/configuration, retaining the configured tool
        # policy. Never copy a transcript or edit global user configuration.
        spec = replace(candidates[0].spec, config_dir=Path(home), app_server_socket=None)
        return create(spec)

    def validate(self, target):
        if target is None:
            raise AgentError('没有找到这个会话，请先查询原会话；不会另开任务代替')
        if target.get('mesh_origin'):
            if not target.get('mesh_id') or self.remote() is None:
                raise AgentError('原会话属于另一台 Runtime，来源转交绑定暂不可用；尚未投递')
            return target
        if target.get('monitoring_only'):
            if not target.get('session_id') or not target.get('desktop_cwd'):
                raise AgentError('原会话缺少编号或目录，尚未投递')
            if target.get('client_open'):
                raise AgentError('这个会话正开在电脑上，手机发的话进不了那个窗口；请到电脑上继续')
            if target.get('desktop_status') in ('running', 'waiting') and not target.get('stale'):
                raise AgentError('原会话还在跑或等待审批，尚未投递；不会启动第二个写入者')
            if any(c.get('borrowed_session') == target.get('session_id')
                   and c.get('borrowed_scope', '') == (target.get('native_scope') or '')
                   and c.get('state') in ('working', 'talking', 'waiting')
                   for c in self.cards.session_targets()):
                raise AgentError('原会话已有续接正在执行或等待审批，尚未再次投递')
            self.adapter(target)
        elif not any(a.name == target.get('agent') for a in self.agents()):
            raise AgentError('原执行端暂不可用，尚未投递')
        return target

    def execution_agent(self, card, fallback):
        if not card.get('borrowed_scope'):
            return fallback
        source = self.target(card.get('desktop_card'), card.get('conversation', 'default'))
        if (source is None or source.get('native_scope') != card['borrowed_scope']
                or source.get('session_id') != card.get('borrowed_session')
                or source.get('desktop_cwd') != card.get('borrowed_cwd')):
            raise AgentError('原续接绑定已变化，尚未投递；不会改用其他会话')
        return self.adapter(source, bind=True)

    def search(self, conversation, query='', limit=8):
        if not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 20:
            raise AgentError('会话查询格式无效')
        terms, rows, candidates = [_words(term) for term in query.split() if _words(term)], [], []
        for target in self.targets(conversation).values():
            if not target.get('agent'):
                continue
            source_name = target.get('origin_name') or target.get('source_name') or self.config.name
            text = ' '.join(str(target.get(k) or '') for k in ('title','project','desktop_cwd','worker_cwd','agent')) + ' ' + source_name
            if any(term not in _words(text) for term in terms):
                continue
            candidates.append(target)
        candidates.sort(key=lambda r: (-r.get('updated_at', 0), r['id']))
        # Only probe the bounded result window; old transcript volume must not
        # multiply adapter/capability checks on every owner message.
        for target in candidates[:limit]:
            source_name = target.get('origin_name') or target.get('source_name') or self.config.name
            reason = ''
            try:
                self.validate(target)
            except (AgentError, OSError, ValueError) as error:
                reason = str(error)
            state = target.get('state')
            if target.get('monitoring_only'):
                state = {'idle':'turn_ended', 'running':'working', 'waiting':'waiting'}.get(target.get('desktop_status'), 'unknown')
            if target.get('stale'):
                state = 'unknown'
            delivery = 'waiting_for_approval' if target.get('approval') else target.get('relay_state') or 'not_submitted'
            if (target.get('dispatch') or {}).get('state') == 'unknown':
                delivery = 'acceptance_unknown'
            rows.append(dict(target=target['id'], title=target.get('title'), agent=target.get('agent'),
                project=target.get('project') or Path(target.get('desktop_cwd') or target.get('worker_cwd') or '.').name,
                state=state, delivery_state=delivery, source_name=source_name,
                runtime=target.get('mesh_origin') or self.authority, native=bool(target.get('monitoring_only')),
                continuation='native-session' if target.get('monitoring_only') else 'managed-task',
                resumable=not bool(reason), reason=reason, updated_at=target.get('updated_at', 0)))
        return {'matches': rows, 'more': len(candidates) > limit,
                'selection': 'explicit-target-required', 'policy': 'session-gateway-v1'}

    def render(self, conversation, query):
        return json.dumps(self.search(conversation, query), ensure_ascii=False)
