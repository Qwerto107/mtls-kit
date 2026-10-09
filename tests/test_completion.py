import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mtls


class CompletionTests(unittest.TestCase):
    def setUp(self):
        # 每个测试使用独立目录，不接触真实配置、证书或密码。
        self.temporary = tempfile.TemporaryDirectory(prefix='mtls-completion-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.data = self.home / 'CA data'
        self.data.mkdir()
        self.index = self.data / 'index.txt'
        self.index.write_text(
            'V\t350101000000Z\t\t01\tunknown\t/CN=admin-laptop\n'
            'R\t350101000000Z\t250101000000Z\t02\tunknown\t/CN=admin-laptop\n'
            'V\t350101000000Z\t\t03\tunknown\t/CN=phone\n',
            encoding='utf-8',
        )
        self.config = self.home / '.config' / 'mtls-kit' / 'config.json'
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({'data_dir': str(self.data)}), encoding='utf-8')
        for name, value in (('CONFIG_FILE', self.config), ('DEFAULT_DATA', self.home / 'default')):
            setting = patch.object(mtls, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def candidates(self, *words):
        return mtls.completion_candidates(list(words), len(words) - 1)

    def test_commands_and_argument_context(self):
        self.assertEqual(self.candidates('mtls', 're'), ('plain', ['renew', 'revoke']))
        # 全局参数的值即使与命令同名，也不应被误认为子命令。
        self.assertIn('issue', self.candidates('mtls', '--data-dir', 'issue', '')[1])
        options = self.candidates('mtls', 'revoke', 'phone', '--')[1]
        self.assertIn('--reason', options)
        self.assertIn('--all', options)
        self.assertNotIn('--data-dir', options)
        self.assertNotIn('--all', self.candidates('mtls', 'issue', 'phone', '--')[1])
        self.assertEqual(self.candidates('mtls', 'completion', 'b'), ('plain', ['bash']))
        self.assertNotIn('--complete', self.candidates('mtls', 'completion', 'bash', '--')[1])

    def test_choices_and_free_text(self):
        for command in ('init', 'issue', 'renew'):
            self.assertEqual(self.candidates('mtls', command, '--key-algorithm', 'p')[1], ['p256', 'p384', 'p521'])
        self.assertEqual(self.candidates('mtls', 'revoke', 'phone', '--reason', 'key')[1], ['keyCompromise'])
        self.assertEqual(self.candidates('mtls', 'init', '--cn', '')[1], [])
        self.assertEqual(self.candidates('mtls', 'issue', '--days', '')[1], [])

    def test_devices_and_directory_priority(self):
        self.assertEqual(self.candidates('mtls', 'renew', '')[1], ['admin-laptop', 'phone'])
        self.assertEqual(self.candidates('mtls', 'list', '--name', 'a')[1], ['admin-laptop'])
        alternate = self.home / 'other CA'
        alternate.mkdir()
        (alternate / 'index.txt').write_text('V\t350101000000Z\t\t04\tunknown\t/CN=other\n')
        for flags in (['--data-dir', str(alternate)], ['--data-dir=' + str(alternate)], ['--data-dir', '=', str(alternate)]):
            self.assertEqual(self.candidates('mtls', *flags, 'show', '')[1], ['other'])
        self.assertEqual(self.candidates('mtls', 'show', 'phone', '')[1], [])

    def test_missing_and_corrupt_state_is_quiet(self):
        for content in ('{broken', json.dumps({'data_dir': str(self.home / 'missing')})):
            self.config.write_text(content)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                mtls.complete(argparse.Namespace(complete=['2', 'mtls', 'revoke', '']))
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(self.candidates('mtls', 're')[1], ['renew', 'revoke'])

    def test_read_only_and_no_password_or_openssl(self):
        before = {file: file.read_bytes() for file in (self.config, self.index)}
        with patch.object(mtls, 'password', side_effect=AssertionError('password requested')), \
             patch.object(mtls, 'run', side_effect=AssertionError('OpenSSL called')):
            self.assertIn('phone', self.candidates('mtls', 'revoke', '')[1])
        self.assertEqual(before, {file: file.read_bytes() for file in before})
        self.config.write_text('{broken')
        with patch.object(sys, 'argv', ['mtls.py', 'completion', 'bash']), \
             patch.object(mtls, 'Authority', side_effect=AssertionError('CA accessed')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(mtls.main(), 0)
        self.assertIn('complete -F _mtls_complete mtls', output.getvalue())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), '需要原生 Bash 环境')
    def test_bash_integration_and_path_quoting(self):
        # 在带空格及单引号的脚本路径运行，验证 Shell 转义和实际参数传递。
        script_path = self.home / "tool ' copy.py"
        shutil.copyfile(mtls.__file__, script_path)
        environment = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1')
        script = subprocess.check_output([sys.executable, str(script_path), 'completion', 'bash'], env=environment, text=True)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)

        def shell_candidates(*words):
            # compopt 仅允许交互补全调用，测试中替换它以直接执行同一补全函数。
            body = script + '\ncompopt() { :; }\nset -u\n'
            body += 'COMP_WORDS=(' + shlex.join(words) + ')\n'
            body += f'COMP_CWORD={len(words) - 1}\n_mtls_complete\n'
            body += 'for word in "${COMPREPLY[@]}"; do printf "%s\\0" "$word"; done\n'
            result = subprocess.check_output(['bash', '-c', body], env=environment)
            return [value.decode() for value in result.split(b'\0') if value]

        self.assertEqual(shell_candidates('mtls', 're'), ['renew', 'revoke'])
        self.assertEqual(shell_candidates('mtls', 'issue', '--key-algorithm', 'p'), ['p256', 'p384', 'p521'])
        self.assertEqual(shell_candidates('mtls', 'revoke', 'a'), ['admin-laptop'])
        self.assertEqual(shell_candidates('mtls', 'issue', '--key-algorithm', '=', 'p'), ['p256', 'p384', 'p521'])
        self.assertEqual(shell_candidates('mtls', 'issue', '--key-algorithm=p'), ['--key-algorithm=p256', '--key-algorithm=p384', '--key-algorithm=p521'])
        self.assertEqual(shell_candidates('mtls', '--data-dir', str(self.home / 'CA')), [str(self.data)])
        self.assertEqual(shell_candidates('mtls', '--data-dir', '~/CA'), [str(self.data)])
        self.assertEqual(shell_candidates('mtls', '--data-dir=' + str(self.home / 'CA')), ['--data-dir=' + str(self.data)])
        self.assertEqual(shell_candidates('mtls', '-d=' + str(self.home / 'CA')), ['-d=' + str(self.data)])
        self.assertEqual(shell_candidates('mtls', 'issue', '-k', 'p'), ['p256', 'p384', 'p521'])
        self.assertEqual(shell_candidates('mtls', '-d', str(self.data), 'list', '-n', 'a'), ['admin-laptop'])
        secret_file = self.home / 'secret $(touch unexpected).txt'
        secret_file.touch()
        self.assertEqual(shell_candidates('mtls', 'export', 'phone', '--output', str(self.home / 'secret')), [str(secret_file)])
        self.assertEqual(shell_candidates('mtls', 'export', 'phone', '-o', str(self.home / 'secret')), [str(secret_file)])
        self.assertFalse((Path.cwd() / 'unexpected').exists())
        self.config.write_text('{broken')
        self.assertEqual(shell_candidates('mtls', 'revoke', ''), [])


if __name__ == '__main__':
    unittest.main()
