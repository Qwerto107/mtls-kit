import contextlib
import datetime as dt
import io
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


@unittest.skipUnless(shutil.which('openssl'), '需要 OpenSSL 环境')
class ExportPasswordTests(unittest.TestCase):
    def setUp(self):
        version = subprocess.check_output(['openssl', 'version'], text=True)
        if not version.startswith('OpenSSL 3.'):
            self.skipTest('需要 OpenSSL 3.x')
        # 临时生成加密私钥和匹配证书，只验证导出流程，不访问真实 CA 数据。
        self.temporary = tempfile.TemporaryDirectory(prefix='mtls-export-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'clients' / 'device' / '01'
        self.directory.mkdir(parents=True)
        self.key = self.directory / 'client.key'
        self.secret = 'client-test-password'
        environment = dict(os.environ, MTLS_KIT_CLIENT_PASSWORD=self.secret)
        commands = [
            ['openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-aes-256-cbc', '-pass', 'env:MTLS_KIT_CLIENT_PASSWORD', '-out', str(self.key)],
            ['openssl', 'req', '-new', '-x509', '-key', str(self.key), '-passin', 'env:MTLS_KIT_CLIENT_PASSWORD', '-subj', '/CN=device', '-days', '1', '-out', str(self.directory / 'client.crt')],
        ]
        for command in commands:
            subprocess.run(command, env=environment, check=True, capture_output=True, timeout=10)
        shutil.copyfile(self.directory / 'client.crt', self.root / 'ca.crt')
        self.output = self.root / 'exports' / 'new.p12'
        args = mtls.parser().parse_args(['-d', str(self.root), 'export', 'device', '-o', str(self.output)])
        self.authority = mtls.Authority(args)
        entry = {'name': 'device', 'serial': '01', 'status': 'V', 'expires': dt.datetime.now(mtls.UTC) + dt.timedelta(days=1)}
        # 跳过与本次密码顺序无关的 CA 数据库校验，解密和 P12 打包均使用真实 OpenSSL。
        for method, value in (('require_ca', None), ('select', entry)):
            setting = patch.object(self.authority, method, return_value=value)
            setting.start()
            self.addCleanup(setting.stop)

    def test_wrong_password_fails_before_p12_prompt_or_output(self):
        original_key = self.key.read_bytes()
        with patch.object(mtls.getpass, 'getpass', return_value='wrong-password') as prompts:
            with self.assertRaisesRegex(mtls.ToolError, '无法读取或解密客户端私钥'):
                self.authority.export()
        self.assertEqual(prompts.call_count, 1)
        self.assertIn('客户端私钥密码', prompts.call_args.args[0])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.parent.exists())
        self.assertEqual(self.key.read_bytes(), original_key)

    def test_correct_password_exports_with_new_password(self):
        original_key = self.key.read_bytes()
        new_secret = 'new-p12-password'
        with patch.object(mtls.getpass, 'getpass', side_effect=[self.secret, new_secret, new_secret]) as prompts, \
             contextlib.redirect_stdout(io.StringIO()):
            self.authority.export()
        self.assertEqual(prompts.call_count, 3)
        environment = dict(os.environ, MTLS_KIT_P12_PASSWORD=new_secret)
        result = subprocess.run(['openssl', 'pkcs12', '-in', str(self.output), '-passin', 'env:MTLS_KIT_P12_PASSWORD', '-noout'], env=environment, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.key.read_bytes(), original_key)

    def test_password_file_failure_does_not_read_new_password_file(self):
        password_file = self.root / 'wrong-password'
        password_file.write_text('wrong-password\n')
        password_file.chmod(0o600)
        self.authority.args.client_password_file = str(password_file)
        self.authority.args.p12_password_file = str(self.root / 'missing-p12-password')
        with patch.object(mtls.getpass, 'getpass', side_effect=AssertionError('unexpected prompt')):
            with self.assertRaisesRegex(mtls.ToolError, '无法读取或解密客户端私钥'):
                self.authority.export()
        self.assertFalse(self.output.parent.exists())

    def test_damaged_or_missing_key_fails_before_new_password(self):
        for content in (b'not a private key', None):
            with self.subTest(content=content):
                if content is None:
                    self.key.unlink()
                else:
                    self.key.write_bytes(content)
                with patch.object(mtls.getpass, 'getpass', return_value=self.secret) as prompts:
                    with self.assertRaisesRegex(mtls.ToolError, '无法读取或解密客户端私钥'):
                        self.authority.export()
                self.assertEqual(prompts.call_count, 1)
                self.assertFalse(self.output.parent.exists())

    def test_wrong_password_preserves_existing_output(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b'preserve existing output')
        with patch.object(mtls.getpass, 'getpass', return_value='wrong-password') as prompts:
            with self.assertRaises(mtls.ToolError):
                self.authority.export()
        self.assertEqual(prompts.call_count, 1)
        self.assertEqual(self.output.read_bytes(), b'preserve existing output')


if __name__ == '__main__':
    unittest.main()
