"""Render an idempotent EOF SSH export block; stdin/stdout only, never reload sshd."""
import sys
from pathlib import Path

BEGIN = '# BEGIN alpha-csv-export managed block'
END = '# END alpha-csv-export managed block'
TEMPLATE = Path(__file__).resolve().parents[1] / 'deploy/ssh-export/sshd_config'


def remove(original):
    if original.count(BEGIN) != original.count(END) or original.count(BEGIN) > 1:
        raise ValueError('Malformed or duplicate managed block')
    if BEGIN in original:
        before, block = original.split(BEGIN)
        _, after = block.split(END)
        if after.strip():
            raise ValueError('Managed block must remain at EOF; review later directives manually')
        return before.rstrip() + '\n'
    return original


def render(original, template):
    original = remove(original)
    if not template.startswith('#') or '\nMatch User ' not in template or '\nMatch all' in template:
        raise ValueError('Expected an EOF-only user Match template')
    return original.rstrip() + '\n\n' + BEGIN + '\n' + template.rstrip() + '\n' + END + '\n'


def main():
    try:
        original = sys.stdin.read()
        if sys.argv[1:] == ['--remove']:
            sys.stdout.write(remove(original))
        elif sys.argv[1:]:
            raise ValueError('Only --remove is supported')
        else:
            sys.stdout.write(render(original, TEMPLATE.read_text(encoding='utf-8')))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
