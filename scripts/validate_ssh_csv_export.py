"""Root-only disposable Linux SSH/ACL acceptance; never connects to a VPS.

Run on an ephemeral CI runner with openssh-server and this package installed.
"""
import csv
import io
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from render_ssh_export_config import remove, render

from perpetual_alpha.examples.pas_research_fixture import create
from perpetual_alpha.venue_collection import Capture


def run(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {result.stderr}')
    return result


def cleanup_account(account):
    """Drain real PAM/user-manager processes for our disposable UID, then delete it."""
    entry = pwd.getpwnam(account)
    if not account.startswith(('alpha_csv_test_', 'alpha_admin_test_')) or entry.pw_uid == 0:
        raise RuntimeError('Refusing to clean an account not owned by this test')
    uid = str(entry.pw_uid)
    if shutil.which('loginctl'):
        sessions = subprocess.run(['loginctl', 'terminate-user', uid], capture_output=True,
                                  text=True, timeout=10)
        print(f'cleanup {account}: loginctl returncode={sessions.returncode}, '
              f'stderr={sessions.stderr!r}', flush=True)
    # loginctl may be unavailable/no session registered; enforce the same postcondition by UID.
    for sig in ('TERM', 'KILL'):
        killed = subprocess.run(['pkill', f'-{sig}', '-u', uid], capture_output=True, text=True, timeout=10)
        if killed.returncode not in (0, 1):
            raise RuntimeError(f'Cannot stop disposable UID {uid}: {killed.stderr}')
        for _ in range(50):
            remaining = subprocess.run(['pgrep', '-u', uid], capture_output=True, text=True, timeout=5)
            if remaining.returncode == 1:
                run('userdel', account)
                return
            if remaining.returncode != 0:
                raise RuntimeError(f'Cannot inspect disposable UID {uid}: {remaining.stderr}')
            time.sleep(0.1)
    raise RuntimeError(f'Disposable UID {uid} still has processes; refusing userdel')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run only as root on a disposable Linux test runner')
    name = f'alpha_csv_test_{os.getpid()}'
    root = Path(tempfile.mkdtemp(prefix='alpha-ssh-test-'))
    daemon = None
    created = []
    admin = f'alpha_admin_test_{os.getpid()}'
    collector = None
    try:
        root.chmod(0o755)
        create(root / 'public.sqlite3')
        collector = Capture(root / 'public.sqlite3', 'pas')
        for path in root.glob('public.sqlite3*'):
            path.chmod(0o444)
        lock = root / 'export.lock'
        lock.touch(mode=0o444)
        # Same provisioning as production: system user, unusable '*' hash, no passwd -l.
        for account in (name, admin):
            run('useradd', '--system', '--user-group', '--no-create-home', '--home-dir', str(root),
                '--shell', '/bin/sh', '--password', '*', account)
            created.append(account)
            before = run('getent', 'passwd', account).stdout
            for _ in range(2):
                run('usermod', '--password', '*', account)
            assert run('getent', 'passwd', account).stdout == before
            assert run('getent', 'shadow', account).stdout.split(':')[1] == '*'
        user = pwd.getpwnam(name)
        for key in ('host', 'client'):
            run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(root / key))
        authorized = root / 'authorized_keys'
        authorized.write_text('restrict ' + (root / 'client.pub').read_text())
        authorized.chmod(0o644)
        checkout = Path(__file__).resolve().parents[1]
        module_path = root / 'lib'
        shutil.copytree(checkout / 'src/perpetual_alpha', module_path / 'perpetual_alpha')
        launcher = root / 'launch.py'
        launcher.write_text(
            f'import sys; sys.path.insert(0, {str(module_path)!r})\n'
            'from pathlib import Path\nfrom perpetual_alpha import pas_csv_export as e\n'
            f'e.DATABASE=Path({str(root / "public.sqlite3")!r})\n'
            f'e.LOCK=Path({str(lock)!r})\nsys.exit(e.main())\n'
        )
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
        config = root / 'sshd_config'
        # Use Ubuntu's installed main config and actual PAM stack, without touching either.
        Path('/run/sshd').mkdir(exist_ok=True)
        original = Path('/etc/ssh/sshd_config').read_text()
        include = 'Include /etc/ssh/sshd_config.d/*.conf'
        assert include in original
        assert original.index(include) < original.index('UsePAM yes')
        assert original.index(include) < original.index('Subsystem')
        snippets = root / 'sshd_config.d'
        snippets.mkdir()
        (snippets / '10-test-globals.conf').write_text(
            f'Port {port}\nListenAddress 127.0.0.1\nHostKey {root / "host"}\n'
            f'PidFile {root / "pid"}\nPermitUserEnvironment no\nAllowUsers {name} {admin}\n'
            f'AuthorizedKeysFile {authorized}\nLogLevel ERROR\n'
        )
        baseline = original.replace(include, f'Include {snippets}/*.conf')
        baseline_path = root / 'baseline.conf'
        baseline_path.write_text(baseline)
        template = (checkout / 'deploy/ssh-export/sshd_config').read_text()
        template = (template.replace('Match User alpha_export', f'Match User {name}')
                    .replace('/usr/local/bin/alpha-csv-command', f'{sys.executable} -I {launcher}')
                    .replace('/etc/ssh/authorized_keys/alpha_export', str(authorized)))
        rendered = render(baseline, template)
        assert render(rendered, template) == rendered
        assert remove(rendered) == baseline.rstrip() + '\n'
        assert remove(remove(rendered)) == remove(rendered)
        config.write_text(rendered)
        # OpenSSH restores the outer file's Match state after each Include.
        # The historical drop-in is valid; verify its behavior instead of inventing a syntax error.
        connection = f'user={admin},host=localhost,addr=127.0.0.1'
        admin_before = run('/usr/sbin/sshd', '-T', '-f', str(baseline_path), '-C', connection).stdout
        included = snippets / '99-included-export.conf'
        included.write_text(template + '\nMatch all\n')
        include_result = subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(baseline_path)],
                                        capture_output=True, text=True, timeout=10)
        print(f'include.returncode={include_result.returncode!r}', flush=True)
        print(f'include.stdout={include_result.stdout!r}', flush=True)
        print(f'include.stderr={include_result.stderr!r}', flush=True)
        assert include_result.returncode == 0, 'Include must restore the outer global context'
        include_export = run('/usr/sbin/sshd', '-T', '-f', str(baseline_path), '-C',
                             f'user={name},host=localhost,addr=127.0.0.1').stdout
        include_admin = run('/usr/sbin/sshd', '-T', '-f', str(baseline_path), '-C', connection).stdout
        assert include_admin == admin_before, 'Included Match changed administrator settings'
        included.unlink()
        # The measured Match-all example is accepted too; preserve that observation.
        all_path = root / 'match-all-same-file.conf'
        all_path.write_text(baseline + '\n' + template + '\nMatch all\nUsePAM yes\n')
        all_result = subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(all_path)],
                                    capture_output=True, text=True, timeout=10)
        print(f'match_all.returncode={all_result.returncode!r}', flush=True)
        print(f'match_all.stdout={all_result.stdout!r}', flush=True)
        print(f'match_all.stderr={all_result.stderr!r}', flush=True)
        assert all_result.returncode == 0, 'Measured Match-all example changed behavior'
        # Real negative control: a global-only directive directly in a named User block.
        invalid_path = root / 'invalid-user-match.conf'
        invalid_path.write_text(baseline + '\n' + template + '\nUsePAM yes\n')
        invalid = subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(invalid_path)],
                                 capture_output=True, text=True, timeout=10)
        print(f'invalid.returncode={invalid.returncode!r}', flush=True)
        print(f'invalid.stdout={invalid.stdout!r}', flush=True)
        print(f'invalid.stderr={invalid.stderr!r}', flush=True)
        assert invalid.returncode != 0, 'Global directive directly inside Match User was accepted'
        assert all(part in invalid.stderr for part in (str(invalid_path), 'UsePAM', 'Match')), (
            'Expected a UsePAM/Match-context rejection in the negative-control file')
        for selected in (name, admin):
            effective = run('/usr/sbin/sshd', '-T', '-f', str(config), '-C',
                            f'user={selected},host=localhost,addr=127.0.0.1').stdout
            assert 'usepam yes' in effective
            if selected == admin:
                before = run('/usr/sbin/sshd', '-T', '-f', str(baseline_path), '-C',
                             f'user={selected},host=localhost,addr=127.0.0.1').stdout
                assert effective == before, 'Admin effective configuration changed'
            else:
                assert effective == include_export, 'EOF and included restrictions differ'
                for setting in ('disableforwarding yes', 'permittty no', 'passwordauthentication no',
                                'kbdinteractiveauthentication no', 'authenticationmethods publickey'):
                    assert setting in effective
        Path('/run/sshd').mkdir(exist_ok=True)
        run('/usr/sbin/sshd', '-t', '-f', str(config))
        daemon = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(config)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(50):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        ssh = ['ssh', '-F', '/dev/null', '-p', str(port), '-i', str(root / 'client'),
               '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=no',
               '-o', 'UserKnownHostsFile=/dev/null', '-o', 'BatchMode=yes',
               f'{name}@127.0.0.1']
        admin_ssh = [*ssh[:-1], f'{admin}@127.0.0.1']
        assert run(*admin_ssh, 'printf admin-ok').stdout == 'admin-ok'
        for dataset in ('observations', 'funding', 'minute-panel'):
            result = run(*ssh, f'export --all --symbol BTC --dataset {dataset}')
            rows = list(csv.DictReader(io.StringIO(result.stdout)))
            assert rows and all(r['symbol'] == 'pas:BTC' for r in rows)
        result = run(*ssh, 'export --from 2026-10-19T00:01:00Z --to 2026-10-19T00:02:00Z --symbol BTC')
        assert len(list(csv.DictReader(io.StringIO(result.stdout)))) == 2
        for command in ('', 'id', 'sh', 'export --all; id', 'export --all --database /etc/passwd',
                        'internal-sftp', 'scp -f /etc/passwd'):
            result = subprocess.run([*ssh, command], capture_output=True, text=True, timeout=10)
            assert result.returncode != 0, command
        result = subprocess.run([*ssh[:-1], '-N', '-o', 'ExitOnForwardFailure=yes',
                                 '-R', '0:localhost:22', ssh[-1]], capture_output=True, timeout=10)
        assert result.returncode != 0
        result = subprocess.run([*ssh[:-1], '-tt', ssh[-1], 'export --all'],
                                capture_output=True, text=True, timeout=10)
        assert 'PTY allocation request failed' in result.stderr
        result = subprocess.run([*ssh[:-1], '-o', 'PreferredAuthentications=password',
                                 '-o', 'PubkeyAuthentication=no', ssh[-1], 'export --all'],
                                capture_output=True, timeout=10)
        assert result.returncode != 0
        result = subprocess.run([*ssh[:-1], '-s', ssh[-1], 'sftp'],
                                capture_output=True, timeout=10)
        assert result.returncode != 0
        # Actual OS permission boundary, independently of the command parser.
        def demote():
            os.setgroups([])
            os.setgid(user.pw_gid)
            os.setuid(user.pw_uid)
        for path in (root / 'public.sqlite3', launcher, authorized):
            result = subprocess.run([sys.executable, '-c',
                                     'import sys; open(sys.argv[1],"ab").write(b"x")', str(path)],
                                    preexec_fn=demote, capture_output=True, timeout=10)
            assert result.returncode != 0, path
        result = subprocess.run([sys.executable, '-c',
                                 'import sys; open(sys.argv[1],"w")', str(root / 'forbidden')],
                                preexec_fn=demote, capture_output=True, timeout=10)
        assert result.returncode != 0
        print('SSH/PAM/CSV assertions passed; cleaning disposable sessions', flush=True)
    finally:
        if daemon is not None:
            daemon.terminate()
            daemon.communicate(timeout=10)
        for account in reversed(created):
            cleanup_account(account)
        if collector is not None:
            collector.close()
        shutil.rmtree(root)
    print('PASS: Ubuntu/PAM, admin login, CSV isolation and disposable session cleanup')


if __name__ == '__main__':
    main()
