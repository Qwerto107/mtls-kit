import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mtls


class StopAtPassword(Exception):
    """在创建任何签发数据前停止，以单独验证目录选择。"""


class InitDirectoryTests(unittest.TestCase):
    def setUp(self):
        # 所有配置和证书都放在临时目录，测试不访问真实用户数据。
        self.temporary = tempfile.TemporaryDirectory(prefix='mtls-init-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.default = self.home / 'default CA'
        self.config = self.home / '.config' / 'mtls-kit' / 'config.json'
        for name, value in (('CONFIG_FILE', self.config), ('DEFAULT_DATA', self.default)):
            setting = patch.object(mtls, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def authority(self, *flags):
        args = mtls.parser().parse_args([*flags, 'init', '--cn', 'Test CA', '--key-algorithm', 'p256', '--days', '30'])
        return mtls.Authority(args)

    def choose_directory(self, authority, answers):
        with patch('builtins.input', side_effect=answers) as prompts, \
             patch.object(authority, 'ca_password', side_effect=StopAtPassword), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(StopAtPassword):
                authority.init()
        return prompts

    def test_enter_uses_first_use_default(self):
        authority = self.authority()
        prompts = self.choose_directory(authority, [''])
        self.assertIn(str(self.default), prompts.call_args.args[0])
        self.assertEqual(authority.root, self.default.resolve())
        self.assertFalse(self.default.exists())
        self.assertFalse(self.config.exists())

    def test_enter_uses_saved_default(self):
        saved = self.home / 'saved CA'
        mtls.save_data_directory(saved)
        before = self.config.read_bytes()
        authority = self.authority()
        prompts = self.choose_directory(authority, [''])
        self.assertIn(str(saved), prompts.call_args.args[0])
        self.assertEqual(authority.root, saved.resolve())
        self.assertEqual(self.config.read_bytes(), before)

    def test_manual_directory_is_first_prompt_and_updates_config_path(self):
        target = self.home / 'chosen CA'
        authority = mtls.Authority(mtls.parser().parse_args(['init']))
        prompts = self.choose_directory(authority, [str(target), 'Test CA', '1', '30'])
        self.assertIn('CA 数据目录', prompts.call_args_list[0].args[0])
        self.assertIn('CA 名称', prompts.call_args_list[1].args[0])
        self.assertEqual(authority.root, target.resolve())
        self.assertEqual(authority.config, target / 'openssl.cnf')
        self.assertFalse(target.exists())

    def test_invalid_and_nonempty_paths_retry_without_changes(self):
        existing = self.home / 'existing CA'
        existing.mkdir()
        material = existing / 'ca.key'
        material.write_bytes(b'preserve existing data')
        ordinary_file = self.home / 'not-a-directory'
        ordinary_file.write_bytes(b'preserve file')
        target = self.home / 'fresh CA'
        prompts = self.choose_directory(self.authority(), [str(existing), str(ordinary_file), str(self.home / 'bad$path'), str(target)])
        self.assertEqual(prompts.call_count, 4)
        self.assertEqual(material.read_bytes(), b'preserve existing data')
        self.assertEqual(ordinary_file.read_bytes(), b'preserve file')
        self.assertFalse(target.exists())
        self.assertFalse(self.config.exists())

    def test_explicit_directory_skips_prompt_and_rejects_existing_data(self):
        # 显式参数仍可绕过损坏的默认配置。
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{broken')
        target = self.home / 'explicit CA'
        authority = self.authority('--data-dir', str(target))
        with patch('builtins.input', side_effect=AssertionError('unexpected prompt')), \
             patch.object(authority, 'ca_password', side_effect=StopAtPassword):
            with self.assertRaises(StopAtPassword):
                authority.init()
        target.mkdir()
        marker = target / 'keep.txt'
        marker.write_bytes(b'keep')
        with patch.object(authority, 'ca_password', side_effect=AssertionError('password requested')):
            with self.assertRaises(mtls.ToolError):
                authority.init()
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_relative_and_home_paths_are_normalized(self):
        original = Path.cwd()
        try:
            os.chdir(self.home)
            authority = self.authority()
            self.choose_directory(authority, ['./relative CA'])
            self.assertEqual(authority.root, self.home / 'relative CA')
        finally:
            os.chdir(original)
        with patch.dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home)):
            authority = self.authority()
            self.choose_directory(authority, ['~/home CA'])
            self.assertEqual(authority.root, self.home / 'home CA')

    def test_eof_and_initialization_failure_do_not_switch_default(self):
        mtls.save_data_directory(self.default)
        before = self.config.read_bytes()
        with patch('builtins.input', side_effect=EOFError):
            with self.assertRaises(mtls.ToolError):
                self.authority().init()
        target = self.home / 'failed CA'
        authority = self.authority()
        with patch('builtins.input', return_value=str(target)), \
             patch.object(authority, 'ca_password', return_value='test-password'), \
             patch.object(authority, 'openssl_run', side_effect=mtls.ToolError('simulated failure')):
            with self.assertRaises(mtls.ToolError):
                authority.init()
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(mtls.data_directory(), self.default.resolve())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('openssl'), '需要原生 OpenSSL 环境')
    def test_real_init_saves_selected_directory_for_next_command(self):
        version = subprocess.check_output(['openssl', 'version'], text=True)
        if not version.startswith('OpenSSL 3.'):
            self.skipTest('需要 OpenSSL 3.x')
        # 实际生成一套临时 CA 和 CRL，确认保存目录及后续命令使用同一路径。
        target = self.home / 'selected CA'
        password_file = self.home / 'password'
        password_file.write_text('test-password-123\n')
        password_file.chmod(0o600)
        environment = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(
            [sys.executable, mtls.__file__, '--ca-password-file', str(password_file), 'init'],
            input=str(target) + '\n\n\n\n', text=True, capture_output=True, env=environment, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text())['data_dir'], str(target.resolve()))
        self.assertIn(f'dir = "{target.as_posix()}"', (target / 'openssl.cnf').read_text())
        self.assertTrue((target / 'ca.crt').is_file())
        self.assertTrue((target / 'ca.crl').is_file())
        paths = subprocess.check_output([sys.executable, mtls.__file__, 'paths'], env=environment, text=True)
        self.assertIn(str(target / 'ca.crt'), paths)


if __name__ == '__main__':
    unittest.main()
