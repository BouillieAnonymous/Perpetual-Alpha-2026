import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('render_ssh', ROOT / 'scripts/render_ssh_export_config.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class SSHConfigTests(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / 'deploy/ssh-export/sshd_config').read_text(encoding='utf-8-sig')
        self.base = ('Include /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\n'
                     'Subsystem sftp /usr/lib/openssh/sftp-server\n')

    def test_globals_preserved_and_managed_suffix_idempotent(self):
        rendered = renderer.render(self.base, self.template)
        self.assertTrue(rendered.startswith(self.base))
        self.assertLess(rendered.index('Subsystem'), rendered.index('Match User'))
        self.assertNotIn('\nMatch all', rendered)
        self.assertEqual(rendered, renderer.render(rendered, self.template))
        self.assertEqual(self.base, renderer.remove(rendered))
        self.assertEqual(self.base, renderer.remove(renderer.remove(rendered)))
        updated = renderer.render(rendered, self.template.replace('MaxSessions 1', 'MaxSessions 2'))
        self.assertEqual(updated.count(renderer.BEGIN), 1)
        self.assertIn('MaxSessions 2', updated)

    def test_fail_closed_on_ambiguous_or_non_tail_blocks(self):
        rendered = renderer.render(self.base, self.template)
        for original in (rendered + 'UsePAM no\n', rendered + rendered,
                         self.base + renderer.BEGIN, self.base + renderer.END):
            with self.subTest(original=original), self.assertRaises(ValueError):
                renderer.render(original, self.template)


if __name__ == '__main__':
    unittest.main()
