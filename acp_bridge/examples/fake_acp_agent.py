"""Original, stdlib-only ACP v1 wire fixture. Not an agent runtime.

Only fixed synthetic answers; no model, network, shell, or credential handling.
Scenario and trace path are operator test arguments, never model parameters.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('happy', 'cancel', 'callbacks', 'allow-only',
        'disconnect', 'hang', 'wrong-version', 'wrong-session', 'malformed-json', 'long-line',
        'post-completion'), default='happy')
    parser.add_argument('--trace', type=Path)
    args = parser.parse_args()
    write_lock, state_lock = threading.Lock(), threading.Lock()
    pending = {}
    cancel = threading.Event()
    session = 'fixture_session_1'
    active = False
    initialized = False
    trace_lock = threading.Lock()

    def trace(value):
        if args.trace:
            with trace_lock, args.trace.open('a', encoding='utf-8') as file:
                file.write(json.dumps(value, ensure_ascii=True) + '\n')

    def no_network_or_child(event, data):
        if event.startswith(('socket.', 'subprocess.')) or event in ('os.system','os.fork','os.posix_spawn','os.exec'):
            raise AssertionError('Fixture cannot use network or spawn child processes')
    sys.addaudithook(no_network_or_child)

    def send(message):
        with write_lock:
            trace({'sent': message})
            sys.stdout.write(json.dumps(message, ensure_ascii=True) + '\n')
            sys.stdout.flush()

    def result(ident, value):
        send({'jsonrpc': '2.0', 'id': ident, 'result': value})

    def error(ident, code, message):
        send({'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}})

    def update(text, sid=session):
        send({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid,
             'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}})

    def callback(ident, method, params):
        event = threading.Event()
        with state_lock:
            pending[ident] = (event, [])
        send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        if not event.wait(5):
            raise AssertionError('Bridge did not answer agent callback: ' + method)
        with state_lock:
            _, replies = pending.pop(ident)
        return replies[0]

    def prompt(ident):
        nonlocal active
        try:
            scenario = args.scenario
            if scenario == 'malformed-json':
                with write_lock:
                    sys.stdout.write('{broken ACP message}\n');sys.stdout.flush()
                return
            if scenario == 'long-line':
                with write_lock:
                    sys.stdout.write('x' * 1048577 + '\n');sys.stdout.flush()
                return
            if scenario in ('callbacks', 'allow-only'):
                options = [{'optionId': 'allow', 'name': 'Allow', 'kind': 'allow_once'}]
                if scenario == 'callbacks':
                    options.append({'optionId': 'deny', 'name': 'Reject', 'kind': 'reject_once'})
                reply = callback('permission_1', 'session/request_permission', {'sessionId': session,
                    'toolCall': {'toolCallId': 'fixture_tool_1', 'title': 'Synthetic unapproved operation',
                                 'kind': 'other', 'status': 'pending'}, 'options': options})
                outcome = reply.get('result', {}).get('outcome', {})
                assert outcome.get('outcome') == 'cancelled' or (outcome.get('outcome') == 'selected' and outcome.get('optionId') == 'deny'), 'Permission was granted'
                for ident2, method, params in (
                    ('fs_1','fs/read_text_file',{'sessionId':session,'path':'/synthetic/not-a-real-file'}),
                    ('terminal_1','terminal/create',{'sessionId':session,'command':'synthetic-command-never-run'})):
                    assert 'error' in callback(ident2, method, params), 'Forbidden client capability was provided'
                update('FIXTURE_CALLBACKS_DENIED')
            else:
                update('fixture ', 'wrong_session' if scenario == 'wrong-session' else session)
            if scenario == 'disconnect':
                # Stop the process without an original-prompt result: unknown outcome.
                sys.stdout.flush()
                import os
                os._exit(0)
            if scenario == 'hang':
                cancel.wait(30)
                return
            if scenario == 'cancel':
                if not cancel.wait(10):
                    return
                # ACP v1 permits updates after cancel and before prompt completion.
                update('late-before-cancel-completion')
                result(ident, {'stopReason': 'cancelled'})
            else:
                if scenario not in ('callbacks','allow-only'):
                    update('answer')
                result(ident, {'stopReason': 'end_turn'})
                if scenario == 'post-completion':
                    update('INVALID_POST_COMPLETION')
        except Exception as exc:
            error(ident, -32603, type(exc).__name__ + ': ' + str(exc))
        finally:
            with state_lock:
                active = False

    for line in sys.stdin:
        if len(line.encode('utf-8')) > 65536:
            raise SystemExit('Fixture input frame too large')
        message = json.loads(line)
        trace({'received': message})
        if 'method' not in message:
            with state_lock:
                target = pending.get(message.get('id'))
                if target:
                    target[1].append(message);target[0].set()
            continue
        method, ident, params = message['method'], message.get('id'), message.get('params', {})
        if method == 'initialize':
            assert params.get('protocolVersion') == 1
            initialized = True
            result(ident, {'protocolVersion': 2 if args.scenario == 'wrong-version' else 1,
                'agentCapabilities': {'loadSession': False, 'promptCapabilities': {'image': False, 'audio': False, 'embeddedContext': False}},
                'agentInfo': {'name': 'offline-acp-fixture', 'version': '0.1.0'}, 'authMethods': []})
        elif method == 'session/new':
            assert initialized
            assert params.get('mcpServers') == [], 'Fixture does not accept nested MCP servers'
            assert Path(params.get('cwd', '')).is_absolute()
            result(ident, {'sessionId': session})
        elif method == 'session/prompt':
            assert params.get('sessionId') == session
            assert all(x.get('type') == 'text' for x in params.get('prompt', []))
            with state_lock:
                busy = active
                if not busy:active = True;cancel.clear()
            if busy:error(ident,-32000,'Fixture session already active')
            else:threading.Thread(target=prompt,args=(ident,),daemon=True).start()
        elif method == 'session/cancel':
            assert 'id' not in message, 'session/cancel is a notification'
            assert params.get('sessionId') == session
            cancel.set()
        elif ident is not None:
            error(ident, -32601, 'Method not supported by fixture')

if __name__ == '__main__':
    main()
