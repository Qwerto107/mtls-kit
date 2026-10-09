import builtins
import contextlib
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mtls


class PromptInputTests(unittest.TestCase):
    def test_pipe_input_and_default_are_preserved(self):
        # 非终端输入按行读取，保留既有默认值规则。
        with patch.object(sys, 'stdin', io.StringIO('  value  \n\n')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mtls.prompt_value('Value', 'default', str), 'value')
            self.assertEqual(mtls.prompt_value('Value', 'default', str), 'default')

    def test_missing_readline_falls_back_to_input(self):
        # 模拟没有 readline 的平台，普通输入仍能完成。
        original_import = builtins.__import__

        def without_readline(name, *args, **kwargs):
            if name == 'readline':
                raise ImportError('readline unavailable')
            return original_import(name, *args, **kwargs)

        with patch.object(sys.stdin, 'isatty', return_value=True), \
             patch.object(sys.stdout, 'isatty', return_value=True), \
             patch('builtins.__import__', side_effect=without_readline), \
             patch('builtins.input', return_value='value'):
            self.assertEqual(mtls.prompt_value('Value', None, str), 'value')

    def run_terminal(self, keystrokes, erase=b'\x7f', password=False):
        # 只在隔离伪终端中改删除字符，不更改用户终端，不操作证书。
        import pty
        import termios
        master, slave = pty.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[6][termios.VERASE] = erase
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        code = f'import sys; sys.path.insert(0, {str(Path(mtls.__file__).parent)!r}); import mtls; '
        code += "value=mtls.prompt_value('Value', None, str); print('RESULT:' + repr(value)); "
        if password:
            code += "secret=mtls.password('Secret'); import readline; "
            code += "print('SECRET_IN_HISTORY:' + str(any(readline.get_history_item(i) == secret for i in range(1, readline.get_current_history_length()+1))))"
        environment = dict(os.environ, TERM='xterm', INPUTRC='/dev/null', LC_ALL='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
        # 独立会话避免 getpass 打开外层测试进程的控制终端。
        child = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave, env=environment, start_new_session=True)
        os.close(slave)

        def read_until(marker):
            output = b''
            deadline = time.monotonic() + 5
            while marker not in output:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                    self.fail('Timed out waiting for terminal prompt')
                try:
                    output += os.read(master, 4096)
                except OSError:
                    self.fail('Terminal exited before showing prompt: ' + repr(output))
            return output

        try:
            output = read_until('Value：'.encode())
            os.write(master, keystrokes)
            if password:
                output += read_until('Secret：'.encode())
                os.write(master, b'hidden-test-password\n')
            child.wait(timeout=5)
            while select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            self.assertEqual(child.returncode, 0, repr(output))
            return output.decode('utf-8')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            os.close(master)

    @unittest.skipUnless(os.name == 'posix', '需要 Unix 伪终端和 readline')
    def test_backspace_works_with_mismatched_erase_character(self):
        # 两种常见退格码均应删除字符，而不是进入最终字段。
        for erase, key in ((b'\x7f', b'\x08'), (b'\x08', b'\x7f')):
            with self.subTest(erase=erase, key=key):
                self.assertIn("RESULT:'abX'", self.run_terminal(b'abc' + key + b'X\n', erase))

    @unittest.skipUnless(os.name == 'posix', '需要 Unix 伪终端和 readline')
    def test_unicode_backspace_and_cursor_editing(self):
        self.assertIn("RESULT:'aX'", self.run_terminal('a中'.encode() + b'\x7fX\n'))
        self.assertIn("RESULT:'aXb'", self.run_terminal(b'ab\x1b[DX\n'))

    @unittest.skipUnless(os.name == 'posix', '需要 Unix 伪终端和 readline')
    def test_password_stays_hidden_and_out_of_history(self):
        output = self.run_terminal(b'abc\x08X\n', password=True)
        self.assertIn("RESULT:'abX'", output)
        self.assertNotIn('hidden-test-password', output)
        self.assertIn('SECRET_IN_HISTORY:False', output)


if __name__ == '__main__':
    unittest.main()
