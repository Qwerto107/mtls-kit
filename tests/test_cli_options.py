import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mtls


class CliOptionTests(unittest.TestCase):
    def test_short_options_match_long_options(self):
        # 比较真实解析结果，确保缩写沿用原有校验和默认值。
        pairs = [
            (['--data-dir', '/test/ca', 'status'], ['-d', '/test/ca', 'status']),
            (['init', '--key-algorithm', 'p384', '--days', '3650'], ['init', '-k', 'p384', '-t', '3650']),
            (['issue', 'phone', '--key-algorithm', 'p521', '--days', '365'], ['issue', 'phone', '-k', 'p521', '-t', '365']),
            (['renew', 'phone', '--key-algorithm', 'rsa3072', '--days', '30'], ['renew', 'phone', '-k', 'rsa3072', '-t', '30']),
            (['list', '--name', 'phone'], ['list', '-n', 'phone']),
            (['export', 'phone', '--serial', '01', '--output', 'new.p12'], ['export', 'phone', '-s', '01', '-o', 'new.p12']),
            (['revoke', 'phone', '--all', '--reason', 'keyCompromise'], ['revoke', 'phone', '-a', '-r', 'keyCompromise']),
            (['crl', '--days', '30'], ['crl', '-t', '30']),
        ]
        for command in ('show', 'check', 'revoke', 'paths'):
            pairs.append(([command, 'phone', '--serial', '01'], [command, 'phone', '-s', '01']))
        for long_args, short_args in pairs:
            with self.subTest(command=long_args[0]):
                self.assertEqual(vars(mtls.parser().parse_args(long_args)), vars(mtls.parser().parse_args(short_args)))

    def test_short_options_keep_validation_and_mutual_exclusion(self):
        for args in (['revoke', 'phone', '-a', '-s', '01'], ['issue', 'phone', '-t', '0'], ['init', '-k', 'invalid'], ['export', 'phone']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    mtls.parser().parse_args(args)
                self.assertEqual(result.exception.code, 2)

    def test_version_does_not_require_ca_or_openssl(self):
        for flag in ('-V', '--version'):
            with patch.object(sys, 'argv', ['mtls.py', flag]), \
                 patch.object(mtls, 'Authority', side_effect=AssertionError('CA accessed')), \
                 patch.object(mtls, 'run', side_effect=AssertionError('OpenSSL called')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as result:
                    mtls.main()
                self.assertEqual(result.exception.code, 0)
                self.assertEqual(output.getvalue().strip(), 'mtls-kit 1.0.0')

    def test_status_includes_tool_and_runtime_versions(self):
        # 仅替换外部环境检查，确认 status 保留原字段并新增工具版本。
        with patch.object(sys, 'argv', ['mtls.py', 'status']), \
             patch.object(mtls, 'data_directory', return_value=Path('/test/ca')), \
             patch.object(mtls.Authority, 'require_openssl', return_value='OpenSSL 3.0.0'), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(mtls.main(), 0)
        for label in ('mtls-kit：1.0.0', 'Python：', 'OpenSSL：OpenSSL 3.0.0', 'CA 数据目录：', 'CA 已初始化：'):
            self.assertIn(label, output.getvalue())


if __name__ == '__main__':
    unittest.main()
