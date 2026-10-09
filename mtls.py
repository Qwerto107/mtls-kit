#!/usr/bin/env python3
"""mtls-kit mTLS 证书管理工具：管理 CA、客户端证书、P12 导出和 CRL。"""

import argparse
import datetime as dt
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


UTC = dt.timezone.utc
# 尚未保存默认目录时使用本机路径；配置与证书数据分开存放。
DEFAULT_DATA = Path.home() / '.local' / 'share' / 'mtls-kit'
CONFIG_FILE = Path.home() / '.config' / 'mtls-kit' / 'config.json'
DEFAULT_CA_NAME = 'MTLS Client CA'
DEFAULT_CA_DAYS = 3650
# 客户端默认有效期按每年 365 天计算，签发、续签与 CA 配置共用。
DEFAULT_CLIENT_DAYS = 365 * 3
# CA 默认密钥和客户端共用 P-256 曲线，prime256v1 是 OpenSSL 中的曲线名称。
DEFAULT_EC_CURVE = 'prime256v1'
# CA 和客户端共用算法菜单：显示名称、算法类型、曲线名称或 RSA 位数。
KEY_ALGORITHMS = {
    'p256': ('ECDSA P-256', 'EC', DEFAULT_EC_CURVE),
    'p384': ('ECDSA P-384', 'EC', 'secp384r1'),
    'p521': ('ECDSA P-521', 'EC', 'secp521r1'),
    'rsa3072': ('RSA 3072', 'RSA', '3072'),
    'rsa4096': ('RSA 4096', 'RSA', '4096'),
}
NAME_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')


class ToolError(Exception):
    """可直接展示给使用者的操作错误。"""


def data_directory(explicit=None):
    # 显式参数优先，并且不读取配置，便于临时操作其他 CA 或修复配置。
    if explicit is not None:
        if not explicit.strip():
            raise ToolError('数据目录不能为空')
        return Path(explicit).expanduser().resolve()
    try:
        content = CONFIG_FILE.read_text(encoding='utf-8')
    except FileNotFoundError:
        return DEFAULT_DATA.resolve()
    except (OSError, UnicodeError) as error:
        raise ToolError(f'无法读取默认目录配置 {CONFIG_FILE}：{error}；可用 --data-dir 明确指定目录') from error
    try:
        configuration = json.loads(content)
    except ValueError as error:
        raise ToolError(f'默认目录配置不是有效 JSON：{CONFIG_FILE}；请修复配置或使用 --data-dir') from error
    saved = configuration.get('data_dir') if isinstance(configuration, dict) else None
    # 只接受绝对路径，配置损坏时拒绝回退，避免操作另一套 CA。
    if not isinstance(saved, str) or not saved.strip() or '\0' in saved or not Path(saved).is_absolute():
        raise ToolError(f'默认目录配置中的 data_dir 必须是非空绝对路径：{CONFIG_FILE}')
    return Path(saved).resolve()


def save_data_directory(directory):
    # 原子保存路径，配置不包含密码、私钥或证书内容。
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG_FILE.parent, 0o700)
    content = json.dumps({'data_dir': str(directory)}, ensure_ascii=False, indent=2) + '\n'
    write_file(CONFIG_FILE, content)


def prompt_value(label, default, validate):
    # 空输入使用默认值；必填项没有默认值，无效输入允许重试。
    prompt = f'{label}（默认 {default}）：' if default is not None else f'{label}：'
    while True:
        try:
            value = input(prompt).strip()
        except EOFError as error:
            raise ToolError('输入已结束；请在终端交互输入，或明确提供全部所需参数') from error
        try:
            return validate(value or (str(default) if default is not None else ''))
        except (argparse.ArgumentTypeError, ValueError, ToolError) as error:
            print(f'输入无效：{error}')


def ca_name(value):
    # CN 按 UTF-8 字节限制长度，拒绝控制字符，避免生成无效证书名称。
    value = value.strip()
    # 不同平台的 OpenSSL 对反斜杠转义处理不同，拒绝静默改变名称的输入。
    if '\\' in value:
        raise argparse.ArgumentTypeError('CA 名称不能包含反斜杠')
    if not value or not value.isprintable() or len(value.encode('utf-8')) > 64:
        raise argparse.ArgumentTypeError('CA 名称须为可显示文字，UTF-8 编码长度不能超过 64 字节')
    return value


def choose_key_algorithm(label):
    options = list(KEY_ALGORITHMS)
    print(f'{label}：')
    for number, key in enumerate(options, start=1):
        print(f'  {number}. {KEY_ALGORITHMS[key][0]}')

    def validate(value):
        if value not in [str(number) for number in range(1, len(options) + 1)]:
            raise argparse.ArgumentTypeError(f'请输入 1～{len(options)} 的选项数字')
        return options[int(value) - 1]

    return prompt_value('请选择数字', 1, validate)


def run(command, passwords=None, check=True):
    # 参数列表直接交给进程，禁止 shell 拼接；密码也不进入命令行或日志。
    environment = os.environ.copy()
    if passwords:
        environment.update(passwords)
    try:
        result = subprocess.run(
            [str(value) for value in command],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError(f'无法执行 {command[0]}：{error}') from error
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ToolError(f'{command[0]} 执行失败：\n{detail}')
    return result


def password(label, source=None, confirm=False):
    if source:
        path = Path(source).expanduser()
        # Linux 上拒绝使用可被其他用户读取的密码文件。
        if os.name == 'posix' and path.stat().st_mode & 0o077:
            raise ToolError(f'密码文件权限过宽，请先执行 chmod 600：{path}')
        value = path.read_text(encoding='utf-8').rstrip('\r\n')
    else:
        value = getpass.getpass(f'{label}：')
        if confirm and value != getpass.getpass(f'再次输入{label}：'):
            raise ToolError('两次输入的密码不一致')
    if not value or '\n' in value or '\r' in value or '\0' in value:
        raise ToolError('密码不能为空，也不能包含换行或空字符')
    if confirm and len(value) < 8:
        raise ToolError('新密码至少需要 8 个字符')
    return value


def write_file(path, content, mode=0o600, overwrite=True):
    path = Path(path)
    if path.is_symlink():
        raise ToolError(f'拒绝覆盖符号链接：{path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.mtls-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            if isinstance(content, str):
                content = content.encode('utf-8')
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if overwrite:
            os.replace(temporary, path)
        else:
            # 同目录硬链接原子创建输出，防止并发导出覆盖已有文件。
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def valid_name(value):
    if not NAME_PATTERN.fullmatch(value):
        raise ToolError('设备名仅允许 1～64 位字母、数字、点、下划线和短横线，首位须为字母或数字')
    return value


def parse_time(value):
    # OpenSSL 索引使用 ASN.1 UTC 时间，2050 年起改用四位年份。
    if len(value) == 13:
        year = int(value[:2])
        if year < 50:
            year += 2000
        else:
            year += 1900
        value = str(year) + value[2:]
    return dt.datetime.strptime(value, '%Y%m%d%H%M%SZ').replace(tzinfo=UTC)


@contextmanager
def ca_lock(directory):
    # openssl ca 的文本数据库不支持并发写入，所有修改都持有同一把锁。
    lock = directory / '.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ToolError(f'CA 正在被其他进程使用：{lock}；若上次进程异常退出，请确认进程结束后再删除锁文件') from error
    try:
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


class Authority:
    def __init__(self, args):
        self.args = args
        self.root = data_directory(args.data_dir)
        self.config = self.root / 'openssl.cnf'
        self.openssl = args.openssl

    def openssl_run(self, arguments, passwords=None, check=True):
        return run([self.openssl, *arguments], passwords, check)

    def require_openssl(self):
        version = self.openssl_run(['version']).stdout.strip()
        if not re.match(r'OpenSSL 3\.', version):
            raise ToolError(f'需要 OpenSSL 3.x，当前版本：{version}')
        return version

    def require_ca(self):
        for relative in ('openssl.cnf', 'ca.crt', 'private/ca.key', 'index.txt', 'serial'):
            if not (self.root / relative).is_file():
                raise ToolError(f'CA 尚未完整初始化，缺少 {relative}；请检查 --data-dir 或先执行 init')

    def ca_password(self, confirm=False):
        return password('CA 私钥密码', self.args.ca_password_file, confirm)

    def ca_command(self, arguments, secret):
        return self.openssl_run(
            ['ca', '-config', self.config, '-passin', 'env:MTLS_KIT_CA_PASSWORD', *arguments],
            {'MTLS_KIT_CA_PASSWORD': secret},
        )

    def verify_ca_password(self, secret):
        # 提前验证密码，输错密码时不留下签发中间文件。
        self.openssl_run([
            'pkey', '-in', self.root / 'private/ca.key',
            '-passin', 'env:MTLS_KIT_CA_PASSWORD', '-noout',
        ], {'MTLS_KIT_CA_PASSWORD': secret})

    def rows(self):
        entries = []
        for line in (self.root / 'index.txt').read_text(encoding='utf-8').splitlines():
            fields = line.split('\t')
            if len(fields) != 6:
                raise ToolError('CA 索引格式异常，请从备份恢复，不要继续签发')
            subject = re.search(r'/CN=([^/]+)', fields[5])
            if not subject:
                raise ToolError('CA 索引缺少设备名称')
            entries.append({
                'status': fields[0], 'expires': parse_time(fields[1]),
                'serial': fields[3].upper(), 'name': valid_name(subject.group(1)),
            })
        return entries

    def select(self, name, serial=None):
        valid_name(name)
        entries = [entry for entry in self.rows() if entry['name'] == name]
        if serial:
            serial = serial.replace(':', '').upper()
            if not re.fullmatch(r'[0-9A-F]+', serial):
                raise ToolError('序列号必须是十六进制字符串')
            entries = [entry for entry in entries if entry['serial'] == serial]
        if not entries:
            raise ToolError(f'没有找到设备证书：{name}')
        return max(entries, key=lambda entry: int(entry['serial'], 16))

    def client_dir(self, entry):
        return self.root / 'clients' / entry['name'] / entry['serial']

    def generate_crl(self, secret, days=30):
        # 先生成临时文件，成功后才替换可供部署的 CRL。
        with tempfile.TemporaryDirectory(prefix='.crl-', dir=self.root) as temporary:
            output = Path(temporary) / 'ca.crl'
            self.ca_command(['-gencrl', '-crldays', str(days), '-out', output], secret)
            write_file(self.root / 'ca.crl', output.read_bytes())
        print(f'CRL 已更新，有效期 {days} 天：{self.root / "ca.crl"}')

    def init(self):
        # 禁止重复初始化或覆盖已有数据；失败后的残留也交给管理员检查。
        if self.root.exists() and any(self.root.iterdir()):
            raise ToolError(f'初始化目录必须为空：{self.root}')
        if any(character in self.root.as_posix() for character in '\n\r"$'):
            raise ToolError('CA 目录不能包含换行、双引号或美元符号')
        name = self.args.cn
        if name is None:
            name = prompt_value('CA 名称', DEFAULT_CA_NAME, ca_name)
        algorithm = self.args.key_algorithm
        if algorithm is None:
            algorithm = choose_key_algorithm('CA 密钥算法')
        days = self.args.days
        if days is None:
            days = prompt_value('CA 有效期（天，正整数）', DEFAULT_CA_DAYS, positive_days)
        # 转义 OpenSSL subject 分隔符，名称中的斜杠或加号不能注入其他字段。
        subject_name = name.replace('/', '\\/').replace('+', '\\+')
        algorithm_label, key_type, key_parameter = KEY_ALGORITHMS[algorithm]
        if key_type == 'EC':
            key_options = ['ec', '-pkeyopt', f'ec_paramgen_curve:{key_parameter}']
        else:
            key_options = [f'rsa:{key_parameter}']
        secret = self.ca_password(confirm=True)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        with ca_lock(self.root):
            for directory_name in ('private', 'newcerts', 'clients'):
                (self.root / directory_name).mkdir(mode=0o700)
            # 根据选择生成 CA 密钥，私钥继续加密保存；客户端默认算法不受影响。
            self.openssl_run([
                'req', '-new', '-x509', '-newkey', *key_options, '-sha256',
                '-days', str(days), '-keyout', self.root / 'private/ca.key',
                '-out', self.root / 'ca.crt', '-utf8', '-subj', f'/CN={subject_name}',
                '-passout', 'env:MTLS_KIT_CA_PASSWORD',
                '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                '-addext', 'subjectKeyIdentifier=hash',
            ], {'MTLS_KIT_CA_PASSWORD': secret})
            os.chmod(self.root / 'private/ca.key', 0o600)
            write_file(self.root / 'index.txt', '')
            write_file(self.root / 'index.txt.attr', 'unique_subject = no\n')
            write_file(self.root / 'serial', secrets.token_hex(16).upper() + '\n')
            write_file(self.root / 'crlnumber', '1000\n')
            write_file(self.config, self.ca_config())
            self.generate_crl(secret)
        print(f'CA 初始化完成：{self.root}\n请备份整个目录；不要把 CA 私钥部署到 Nginx')
        print(f'CA 名称：{name}\nCA 密钥算法：{algorithm_label}\nCA 有效期：{days} 天')
        self.paths()
        # 只有完整初始化成功才记住目录，失败或重复 init 不切换默认 CA。
        try:
            save_data_directory(self.root)
        except (OSError, ToolError) as error:
            raise ToolError(
                f'CA 已完成初始化，但默认目录配置保存失败：{error}；'
                f'请继续使用 --data-dir "{self.root}"，不要重新执行 init'
            ) from error
        print(f'默认数据目录已保存：{CONFIG_FILE}')

    def ca_config(self):
        return f'''# 由 mtls.py 生成，仅用于签发客户端证书。
[ca]
default_ca = mtls_ca

[mtls_ca]
dir = "{self.root.as_posix()}"
database = $dir/index.txt
new_certs_dir = $dir/newcerts
certificate = $dir/ca.crt
private_key = $dir/private/ca.key
serial = $dir/serial
crlnumber = $dir/crlnumber
default_md = sha256
default_days = {DEFAULT_CLIENT_DAYS}
default_crl_days = 30
policy = client_policy
x509_extensions = client_cert
crl_extensions = crl_extensions
unique_subject = no
copy_extensions = none

[client_policy]
commonName = supplied

[client_cert]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer

[crl_extensions]
authorityKeyIdentifier = keyid:always
'''

    def pack(self, directory, output, key_secret, p12_secret):
        output = Path(output).expanduser().absolute()
        if output.exists() or output.is_symlink():
            raise ToolError(f'导出文件已存在，拒绝覆盖：{output}')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.p12-', dir=output.parent) as temporary:
            package = Path(temporary) / 'client.p12'
            self.openssl_run([
                'pkcs12', '-export', '-inkey', directory / 'client.key',
                '-in', directory / 'client.crt', '-certfile', self.root / 'ca.crt',
                '-out', package, '-name', directory.parent.name,
                '-passin', 'env:MTLS_KIT_CLIENT_PASSWORD', '-passout', 'env:MTLS_KIT_P12_PASSWORD',
            ], {'MTLS_KIT_CLIENT_PASSWORD': key_secret, 'MTLS_KIT_P12_PASSWORD': p12_secret})
            write_file(output, package.read_bytes(), overwrite=False)
        print(f'浏览器证书包：{output}')

    def issue(self, renewal=False):
        self.require_ca()
        # 命令行已提供的值不再询问；设备名没有默认值，必须输入合法名称。
        name = self.args.name
        if name is None:
            name = prompt_value('设备名', None, valid_name)
        name = valid_name(name)
        algorithm = self.args.key_algorithm
        if algorithm is None:
            algorithm = choose_key_algorithm('客户端密钥算法')
        days = self.args.days
        if days is None:
            days = prompt_value('客户端有效期（天，1～3650）', DEFAULT_CLIENT_DAYS, day_count)
        # 在创建客户端材料之前验证 CA 剩余有效期，避免留下无效签发记录。
        self.openssl_run(['x509', '-in', self.root / 'ca.crt', '-checkend', str(days * 86400)])
        algorithm_label, key_type, key_parameter = KEY_ALGORITHMS[algorithm]
        key_option = 'ec_paramgen_curve' if key_type == 'EC' else 'rsa_keygen_bits'
        with ca_lock(self.root):
            # 历史记录在锁内检查，防止并发命令重复签发同名设备。
            previous = [entry for entry in self.rows() if entry['name'] == name]
            if previous and not renewal:
                raise ToolError('设备名已存在，请使用 renew 续签或使用新的设备名')
            if renewal and (not previous or all(entry['status'] == 'R' for entry in previous)):
                raise ToolError('续签需要至少一张未吊销的历史证书；重新授权设备请使用新的设备名')
            ca_secret = self.ca_password()
            self.verify_ca_password(ca_secret)
            key_secret = password('客户端证书密码', self.args.client_password_file, confirm=True)
            parent = self.root / 'clients' / name
            parent.mkdir(exist_ok=True, mode=0o700)
            # 失败时保留临时目录，防止已签发证书的私钥意外丢失。
            directory = Path(tempfile.mkdtemp(prefix='pending-', dir=parent))
            try:
                # 按选择生成客户端密钥，默认 P-256；私钥始终加密保存。
                self.openssl_run([
                    'genpkey', '-algorithm', key_type, '-pkeyopt', f'{key_option}:{key_parameter}',
                    '-aes-256-cbc', '-pass', 'env:MTLS_KIT_CLIENT_PASSWORD',
                    '-out', directory / 'client.key',
                ], {'MTLS_KIT_CLIENT_PASSWORD': key_secret})
                self.openssl_run([
                    'req', '-new', '-utf8', '-key', directory / 'client.key',
                    '-passin', 'env:MTLS_KIT_CLIENT_PASSWORD', '-out', directory / 'client.csr',
                    '-subj', f'/CN={name}',
                ], {'MTLS_KIT_CLIENT_PASSWORD': key_secret})
                self.ca_command([
                    '-batch', '-notext', '-days', str(days),
                    '-in', directory / 'client.csr', '-out', directory / 'client.crt',
                ], ca_secret)
                result = self.openssl_run(['x509', '-in', directory / 'client.crt', '-noout', '-serial'])
                serial = result.stdout.strip().split('=', 1)[1].upper()
                final_directory = parent / serial
                directory.rename(final_directory)
                directory = final_directory
                self.pack(directory, directory / 'client.p12', key_secret, key_secret)
            except Exception:
                print(f'本次操作文件已保留：{directory}；请先检查 list，避免重复签发', file=sys.stderr)
                raise
        print(f'设备：{name}\n序列号：{serial}\n密钥算法：{algorithm_label}\n有效期：{days} 天')
        self.paths(name, serial)
        if renewal:
            print('旧证书仍保持原状态；安装并验证新证书后，用 revoke --serial 吊销旧证书')

    def export(self):
        self.require_ca()
        entry = self.select(self.args.name, self.args.serial)
        if entry['status'] == 'R' or entry['expires'] <= dt.datetime.now(UTC):
            raise ToolError('不能导出已吊销或已过期的证书，请先签发有效证书')
        key_secret = password('客户端私钥密码', self.args.client_password_file)
        p12_secret = password('新的 P12 导入密码', self.args.p12_password_file, confirm=True)
        self.pack(self.client_dir(entry), self.args.output, key_secret, p12_secret)

    def revoke(self):
        self.require_ca()
        name = valid_name(self.args.name)
        with ca_lock(self.root):
            if self.args.all:
                entries = [entry for entry in self.rows() if entry['name'] == name]
                if not entries:
                    raise ToolError(f'没有找到设备证书：{name}')
            else:
                entries = [self.select(name, self.args.serial)]
            secret = self.ca_password()
            self.verify_ca_password(secret)
            for entry in entries:
                if entry['status'] != 'R':
                    # 使用 OpenSSL 归档证书，即使导出失败也能按序列号吊销。
                    certificate = self.root / 'newcerts' / f'{entry["serial"]}.pem'
                    self.ca_command(['-revoke', certificate, '-crl_reason', self.args.reason], secret)
                print(f'已吊销：{entry["name"]} / {entry["serial"]}')
            self.generate_crl(secret, self.args.crl_days)
        print('请手动将新 CRL 同步到验证端，本地吊销不会自动影响线上服务')

    def list(self):
        self.require_ca()
        print('状态\t设备\t到期时间（UTC）\t序列号')
        for entry in sorted(self.rows(), key=lambda item: (item['name'], int(item['serial'], 16))):
            if self.args.name and entry['name'] != self.args.name:
                continue
            status = '有效'
            if entry['status'] == 'R':
                status = '已吊销'
            elif entry['expires'] <= dt.datetime.now(UTC):
                status = '已过期'
            print(f'{status}\t{entry["name"]}\t{entry["expires"]:%Y-%m-%d %H:%M:%S}\t{entry["serial"]}')
        print(f'\n数据目录：{self.root}')
        self.show_crl()

    def show_crl(self):
        result = self.openssl_run(['crl', '-in', self.root / 'ca.crl', '-noout', '-lastupdate', '-nextupdate'])
        print(result.stdout.strip())

    def paths(self, name=None, serial=None):
        self.require_ca()
        print(f'CA 证书：{self.root / "ca.crt"}\n吊销列表：{self.root / "ca.crl"}')
        if name:
            entry = self.select(name, serial)
            directory = self.client_dir(entry)
            for label, filename in [('客户端证书', 'client.crt'), ('加密私钥', 'client.key'), ('浏览器证书包', 'client.p12')]:
                path = directory / filename
                if path.is_file():
                    print(f'{label}：{path}')
                else:
                    print(f'{label}：文件尚未生成，请检查签发记录')

    def show(self):
        self.require_ca()
        entry = self.select(self.args.name, self.args.serial)
        certificate = self.root / 'newcerts' / f'{entry["serial"]}.pem'
        result = self.openssl_run([
            'x509', '-in', certificate, '-noout', '-subject', '-issuer',
            '-serial', '-dates', '-fingerprint', '-sha256',
            '-ext', 'keyUsage,extendedKeyUsage',
        ])
        print(result.stdout.strip())
        if entry['status'] == 'R':
            print('CA 索引状态：已吊销')
        self.paths(entry['name'], entry['serial'])

    def check(self):
        self.require_ca()
        entry = self.select(self.args.name, self.args.serial)
        certificate = self.root / 'newcerts' / f'{entry["serial"]}.pem'
        result = self.openssl_run([
            'verify', '-purpose', 'sslclient', '-CAfile', self.root / 'ca.crt',
            '-CRLfile', self.root / 'ca.crl', '-crl_check', certificate,
        ])
        print(result.stdout.strip())


def positive_days(value):
    # CA 有效期只要求正整数，不设置工具层面的最大天数。
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('天数必须是整数') from error
    if number < 1:
        raise argparse.ArgumentTypeError('天数必须大于 0')
    return number


def day_count(value):
    # 客户端和 CRL 沿用已有范围，CA 使用独立的正整数校验。
    number = positive_days(value)
    if number > 3650:
        raise argparse.ArgumentTypeError('天数必须在 1～3650 之间')
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--data-dir', help='CA 数据目录；省略时使用已保存目录，无配置时使用 ~/.local/share/mtls-kit')
    result.add_argument('--openssl', default='openssl', help='OpenSSL 3.x 可执行文件')
    result.add_argument('--ca-password-file', help='CA 密码文件，用于自动化；Linux 权限必须为 600')
    commands = result.add_subparsers(dest='command', required=True)
    initialization = commands.add_parser('init', help='初始化加密 CA，并生成首份 CRL')
    initialization.add_argument('--cn', type=ca_name, help='CA 名称；省略时交互输入，回车使用 MTLS Client CA')
    initialization.add_argument('--key-algorithm', choices=KEY_ALGORITHMS, help='CA 密钥算法；省略时用数字交互选择，回车使用 p256')
    initialization.add_argument('--days', type=positive_days, help='CA 有效天数，须为正整数，无工具上限；省略时交互输入，回车使用 3650')
    for command in ('issue', 'renew'):
        issuance = commands.add_parser(command, help='签发设备证书；renew 保留旧证书供安装切换')
        issuance.add_argument(
            'name', nargs='?' if command == 'issue' else None,
            help='设备名，例如 admin-laptop；issue 省略时交互输入',
        )
        issuance.add_argument(
            '--key-algorithm', choices=KEY_ALGORITHMS,
            default=None if command == 'issue' else 'p256',
            help='客户端密钥算法；issue 省略时交互选择，默认 p256',
        )
        issuance.add_argument(
            '--days', type=day_count,
            default=None if command == 'issue' else DEFAULT_CLIENT_DAYS,
            help='客户端证书有效天数；issue 省略时交互输入，回车使用 1095 天（约 3 年）',
        )
        issuance.add_argument('--client-password-file', help='客户端私钥和 P12 的密码文件')
    listing = commands.add_parser('list', help='列出所有历史证书和 CRL 有效期')
    listing.add_argument('--name', help='只显示指定设备')
    descriptions = {'export': '重新导出 P12', 'revoke': '吊销证书并更新 CRL', 'check': '本地检查证书和 CRL', 'show': '查看证书详情和路径'}
    for command in descriptions:
        operation = commands.add_parser(command, help=descriptions[command])
        operation.add_argument('name', help='设备名')
        selection = operation.add_mutually_exclusive_group()
        selection.add_argument('--serial', help='指定历史证书；省略时使用最新序列号')
        if command == 'revoke':
            selection.add_argument('--all', action='store_true', help='吊销该设备全部版本的证书')
            operation.add_argument('--reason', choices=['unspecified', 'keyCompromise', 'superseded', 'cessationOfOperation'], default='unspecified')
            operation.add_argument('--crl-days', type=day_count, default=30)
        if command == 'export':
            operation.add_argument('--client-password-file', help='已有客户端私钥的密码文件')
            operation.add_argument('--output', required=True, help='新的 P12 输出路径，不覆盖已有文件')
            operation.add_argument('--p12-password-file', help='新的 P12 导入密码文件')
    crl = commands.add_parser('crl', help='刷新 CRL，有效期内也需要定期执行')
    crl.add_argument('--days', type=day_count, default=30, help='CRL 有效天数')
    paths = commands.add_parser('paths', help='只输出 CA、CRL 或指定设备的证书路径')
    paths.add_argument('name', nargs='?', help='可选设备名')
    paths.add_argument('--serial', help='指定设备的历史证书序列号')
    commands.add_parser('status', help='查看 Python、OpenSSL、CA 数据位置和初始化状态')
    return result


def main():
    args = parser().parse_args()
    try:
        authority = Authority(args)
        if args.command == 'status':
            print(f'Python：{sys.version.split()[0]}\nOpenSSL：{authority.require_openssl()}')
            print(f'CA 数据目录：{authority.root}\nCA 已初始化：{authority.config.is_file()}')
        else:
            authority.require_openssl()
            if args.command == 'init':
                authority.init()
            elif args.command == 'issue':
                authority.issue()
            elif args.command == 'renew':
                authority.issue(renewal=True)
            elif args.command == 'export':
                authority.export()
            elif args.command == 'revoke':
                authority.revoke()
            elif args.command == 'list':
                authority.list()
            elif args.command == 'check':
                authority.check()
            elif args.command == 'show':
                authority.show()
            elif args.command == 'paths':
                if args.serial and not args.name:
                    raise ToolError('--serial 必须与设备名一起使用')
                authority.paths(args.name, args.serial)
            elif args.command == 'crl':
                authority.require_ca()
                secret = authority.ca_password()
                with ca_lock(authority.root):
                    authority.generate_crl(secret, args.days)
    except KeyboardInterrupt:
        print('\n操作已中断', file=sys.stderr)
        return 130
    except EOFError:
        print('错误：密码输入已结束，操作未完成', file=sys.stderr)
        return 1
    except (ToolError, OSError, ValueError) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
