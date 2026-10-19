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

from perpetual_alpha.examples.pas_research_fixture import create


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30, **kwargs)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run only as root on a disposable Linux test runner')
    name = f'alpha_csv_test_{os.getpid()}'
    root = Path(tempfile.mkdtemp(prefix='alpha-ssh-test-'))
    daemon = None
    created = False
    try:
        root.chmod(0o755)
        create(root / 'public.sqlite3')
        for path in root.glob('public.sqlite3*'):
            path.chmod(0o444)
        lock = root / 'export.lock'
        lock.touch(mode=0o444)
        # Permit public-key authentication with an unusable password hash (not a locked account).
        run('useradd', '--no-create-home', '--home-dir', str(root), '--shell', '/bin/sh',
            '--password', '*', name)
        created = True
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
        template = (checkout / 'deploy/ssh-export/sshd_config').read_text()
        config.write_text(
            f'Port {port}\nListenAddress 127.0.0.1\nHostKey {root / "host"}\n'
            f'PidFile {root / "pid"}\nUsePAM no\nAllowUsers {name}\nLogLevel ERROR\n'
            + template.replace('Match User alpha_export', f'Match User {name}')
            .replace('/usr/local/bin/alpha-csv-command', f'{sys.executable} -I {launcher}')
            .replace('/etc/ssh/authorized_keys/alpha_export', str(authorized))
        )
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
        print('PASS: real SSH CSV, UTC range, command rejection, forwarding denial, read-only OS permissions')
    finally:
        if daemon is not None:
            daemon.terminate()
            daemon.communicate(timeout=10)
        if created:
            run('userdel', name)
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
