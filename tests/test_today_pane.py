"""Exercise the launcher with fake Orca, real viewer processes and real flock."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE_ORCA = r'''#!/usr/bin/env python3
import json, os, pathlib, shlex, subprocess, sys
root = pathlib.Path(os.environ['PANE_TEST_ROOT'])
args = sys.argv[1:]
command = args[1]
def option(name):
    return args[args.index(name) + 1]
def reply(result):
    print(json.dumps({'ok': True, 'result': result}))
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\n')
if command == 'list':
    reply({'terminals': json.loads((root / 'terminals.json').read_text())})
elif command == 'read':
    if (root / 'read_error').exists():
        sys.exit(1)
    if (root / 'screen.json').exists():
        print((root / 'screen.json').read_text())
    else:
        rows = (root / 'screen').read_text().splitlines()
        reply({'terminal': {'tail': rows, 'tailRows': [dict(wrapped=False) for _ in rows]}})
elif command in ('send', 'split'):
    handle = option('--terminal') if command == 'send' else 'new-viewer'
    text = option('--text') if command == 'send' else option('--command')
    if command == 'split':
        terminals = json.loads((root / 'terminals.json').read_text())
        terminals.append(dict(terminals[0], handle=handle, leafId='new-viewer-leaf',
                              agentIdentity=None))
        (root / 'terminals.json').write_text(json.dumps(terminals))
    env = dict(os.environ, ORCA_TERMINAL_HANDLE=handle)
    if not (root / 'no_start').exists():
        with (root / 'viewer.out').open('a') as out:
            process = subprocess.Popen(shlex.split(text), env=env, stdout=out,
                                       stderr=out, start_new_session=True)
        with (root / 'pids').open('a') as out:
            out.write(str(process.pid) + '\n')
    if command == 'split':
        reply({'split': {'handle': handle, 'leafId': 'new-viewer-leaf'}})
    else:
        reply({'terminal': {'handle': handle}})
elif command == 'set-pane-title':
    if (root / 'title_error').exists():
        sys.exit('title delivery failed')
    terminals = json.loads((root / 'terminals.json').read_text())
    for terminal in terminals:
        if terminal['handle'] == option('--terminal'):
            title = option('--title')
            if title:
                terminal['paneTitle'] = title
            else:
                terminal.pop('paneTitle', None)
    (root / 'terminals.json').write_text(json.dumps(terminals))
    reply({})
else:
    sys.exit('unexpected Orca command: ' + repr(args))
'''


@unittest.skipUnless(all(shutil.which(c) for c in
                         ('bash', 'jq', 'flock', 'inotifywait', 'sha256sum')),
                     'requires Linux diary viewer tools')
class TodayPaneTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='today pane test ')
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'checkout with spaces'
        (self.repo / 'scripts').mkdir(parents=True)
        (self.repo / 'diaries').mkdir()
        (self.root / 'runtime').mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('today_pane.sh', 'today_view.sh', 'today_runtime.sh'):
            shutil.copy2(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'diaries' / 'day.md').write_text('# Test diary\n')
        (self.repo / 'today.md').symlink_to('diaries/day.md')
        for name, content in {
            'orca-test': FAKE_ORCA,
            'glow': '#!/bin/sh\ncat today.md\n',
            'clear': '#!/bin/sh\nexit 0\n',
            'tput': '#!/bin/sh\necho 80\n',
        }.items():
            file = self.bin / name
            file.write_text(content)
            file.chmod(0o755)
        self.env = dict(os.environ, PANE_TEST_ROOT=str(self.root),
                        TMPDIR=str(self.root / 'runtime'),
                        PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        ORCA_CLI_COMMAND=str(self.bin / 'orca-test'),
                        ORCA_TERMINAL_HANDLE='self')
        self.terminals = [self.terminal('self', agentIdentity='codex'),
                          self.terminal('restored', paneTitle='today.md')]
        self.save_terminals()
        (self.root / 'screen').write_text('╰─❯                     ─╯')
        self.held_locks = []

    def tearDown(self):
        pids = self.root / 'pids'
        if pids.exists():
            for pid in pids.read_text().splitlines():
                try:
                    os.killpg(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for lock in self.held_locks:
            lock.close()
        self.temporary.cleanup()

    def terminal(self, handle, **extra):
        return dict(handle=handle, leafId=handle + '-leaf', tabId='tab', connected=True,
                    worktreePath=str(self.repo), **extra)

    def save_terminals(self):
        (self.root / 'terminals.json').write_text(json.dumps(self.terminals))

    def launch(self):
        return subprocess.run(['bash', str(self.repo / 'scripts/today_pane.sh')],
                              env=self.env, capture_output=True, text=True, timeout=8)

    def calls(self, command):
        return [args for args in map(json.loads, (self.root / 'calls.jsonl').read_text().splitlines())
                if args[1] == command]

    def hold_viewer_lock(self, handle):
        result = subprocess.run(['bash', '-c',
                                 'source scripts/today_runtime.sh; today_lock_file "viewer:$1"',
                                 'bash', handle], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, check=True)
        file = open(result.stdout.strip(), 'w')
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.held_locks.append(file)

    def stop_viewer(self):
        pid = int((self.root / 'pids').read_text().splitlines()[-1])
        os.killpg(pid, signal.SIGKILL)
        sends = self.calls('send')
        handle = sends[-1][sends[-1].index('--terminal') + 1] if sends else 'new-viewer'
        process = subprocess.run(['bash', '-c',
                                  'source scripts/today_runtime.sh; file=$(today_lock_file "viewer:$1"); flock -w 2 "$file" true',
                                  'bash', handle],
                                 cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def use_screen(self, rows, wrapped=None):
        wrapped = wrapped or [False] * len(rows)
        (self.root / 'screen.json').write_text(json.dumps({'result': {'terminal': {
            'tail': rows, 'tailRows': [dict(wrapped=value) for value in wrapped]}}}))

    def remember(self, handle='restored'):
        terminal = next(t for t in self.terminals if t['handle'] == handle)
        result = subprocess.run(['bash', '-c',
                                 'source scripts/today_runtime.sh; today_remember_pane "$1" "$2"',
                                 'bash', terminal['tabId'], terminal['leafId']],
                                cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restored_title_restarts_viewer_and_second_hook_skips(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls('send')), 1)
        self.assertEqual(len(self.calls('split')), 0)
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(len(self.calls('send')), 1)
        # Check rendering and live redraw as well as process startup.
        output = self.root / 'viewer.out'
        for _ in range(40):
            if output.exists() and '# Test diary' in output.read_text():
                break
            time.sleep(0.05)
        self.assertIn('# Test diary', output.read_text())
        (self.repo / 'diaries/day.md').write_text('# Changed diary\n')
        for _ in range(40):
            if '# Changed diary' in output.read_text():
                break
            time.sleep(0.05)
        self.assertIn('# Changed diary', output.read_text())

    def test_live_viewer_with_different_title_is_detected(self):
        self.terminals[1]['paneTitle'] = 'arbitrary title'
        self.save_terminals()
        self.hold_viewer_lock('restored')
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_stopped_viewer_releases_lock_and_is_restarted(self):
        self.assertEqual(self.launch().returncode, 0)
        self.stop_viewer()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls('send')), 2)
        self.assertFalse(self.calls('split'))

    def test_busy_restored_pane_is_not_sent_shell_command(self):
        (self.root / 'screen').write_text('Running a server...')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))
        self.assertIn('no extra split', result.stderr)

    def test_wrapped_prompt_reuses_restored_pane(self):
        # Actual Orca --screen row layout from the affected pane.
        self.use_screen(['%', '╭─ user ~/diet_log_public ─╮',
                         '╰─❯                         ─', '╯'],
                        [False, False, False, True])
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls('send')), 1)
        self.assertFalse(self.calls('split'))

    def test_hard_newline_is_not_mistaken_for_wrapped_prompt(self):
        self.use_screen(['╰─❯', 'running a program', '╯'])
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_program_and_nonempty_prompts_do_not_receive_commands(self):
        for prompt in ('>>> ', '<html>', '╰─❯ echo hello', '> '):
            with self.subTest(prompt=prompt):
                self.use_screen([prompt])
                self.assertEqual(self.launch().returncode, 0)
                self.assertFalse(self.calls('send'))
                self.assertFalse(self.calls('split'))

    def test_busy_saved_pane_does_not_fall_back_to_legacy_pane(self):
        self.remember()
        self.terminals[1]['agentIdentity'] = 'codex'
        self.terminals.append(self.terminal('legacy', paneTitle='today.md'))
        self.save_terminals()
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_unique_agent_can_recover_missing_caller_handle(self):
        self.env.pop('ORCA_TERMINAL_HANDLE')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls('send')), 1)
        self.assertFalse(self.calls('split'))

    def test_lost_title_and_recreated_handle_reuse_saved_leaf(self):
        self.assertEqual(self.launch().returncode, 0)
        self.stop_viewer()
        self.terminals[1].pop('paneTitle')
        self.terminals[1]['handle'] = 'replacement-handle'
        self.save_terminals()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('send')[-1][3], 'replacement-handle')
        self.assertFalse(self.calls('split'))

    def test_disconnected_saved_pane_does_not_create_duplicate(self):
        self.remember()
        self.terminals[1].pop('paneTitle')
        self.terminals[1]['connected'] = False
        self.save_terminals()
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_unreadable_saved_pane_does_not_create_duplicate(self):
        (self.root / 'read_error').touch()
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_saved_pane_running_an_agent_is_not_sent_a_command(self):
        self.remember()
        self.terminals[1].pop('paneTitle')
        self.terminals[1]['agentIdentity'] = 'codex'
        self.save_terminals()
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_closed_saved_pane_is_replaced_once(self):
        self.remember()
        self.terminals.pop()
        self.save_terminals()
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(len(self.calls('split')), 1)

    def test_missing_caller_with_multiple_agents_does_not_guess_tab(self):
        self.env.pop('ORCA_TERMINAL_HANDLE')
        self.terminals.append(self.terminal('another-agent', agentIdentity='codex',
                                           lastOutputAt=123))
        self.save_terminals()
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_stale_caller_handle_does_not_use_another_agent(self):
        self.env['ORCA_TERMINAL_HANDLE'] = 'stale'
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(self.calls('send'))
        self.assertFalse(self.calls('split'))

    def test_title_delivery_failure_does_not_lose_pane(self):
        self.terminals.pop()
        self.save_terminals()
        (self.root / 'title_error').touch()
        self.assertEqual(self.launch().returncode, 0)
        self.stop_viewer()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls('split')), 1)
        self.assertEqual(len(self.calls('send')), 1)

    def test_pending_start_does_not_create_more_panes(self):
        self.terminals.pop()
        self.save_terminals()
        (self.root / 'no_start').touch()
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        (self.root / 'screen').write_text('Starting viewer...')
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(len(self.calls('split')), 1)
        self.assertFalse(self.calls('send'))

    def test_other_tab_viewer_does_not_suppress_current_tab(self):
        self.terminals[1]['tabId'] = 'other-tab'
        self.save_terminals()
        self.hold_viewer_lock('restored')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls('split')), 1)

    def test_simultaneous_hooks_start_one_viewer(self):
        command = ['bash', str(self.repo / 'scripts/today_pane.sh')]
        first = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        for process in (first, second):
            _, error = process.communicate(timeout=8)
            output = self.root / 'viewer.out'
            self.assertEqual(process.returncode, 0,
                             error + (output.read_text() if output.exists() else ''))
        self.assertEqual(len(self.calls('send')), 1)


if __name__ == '__main__':
    unittest.main()
