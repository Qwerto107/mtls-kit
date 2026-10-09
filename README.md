# mtls-kit 证书管理

mtls-kit 是一个轻量级的 mTLS 客户端证书管理工具，用于为每台设备签发独立证书，并管理证书的续签、导出和吊销。
它将常用的 OpenSSL 操作整理为一个 Python 命令行脚本，支持交互输入和参数调用，方便在管理员电脑或服务器上管理证书材料。

## 适用场景

适合需要客户端证书验证的内部应用、运维控制台和私有 API。
每台设备使用独立证书；设备更换或证书到期时续签，设备丢失时吊销，再将公开 CA 证书和 CRL 同步到 Nginx 等验证端。
客户端证书验证与业务应用的登录、用户权限检查共同构成访问控制流程。

## 主要功能

- 初始化专用 CA，设置名称、密钥算法和有效期。
- 按设备签发客户端证书，续签时保留旧版本和历史序列号。
- 导出包含客户端证书、私钥和 CA 证书的 P12 安装包。
- 按序列号或整台设备吊销证书，生成和刷新 CRL。
- 查看证书清单、详细信息和输出路径，执行本地证书校验。
- 保存默认数据目录，支持交互操作和密码文件形式的非交互调用。

CA 和客户端默认使用 ECDSA P-256，也可选择 P-384、P-521、RSA 3072 或 RSA 4096。
CA 与客户端 PEM 私钥均加密保存，导出的 P12 使用密码保护；设备名称、有效期和文件覆盖等操作均有校验。

## 运行要求与工具范围

使用 Python 标准库调用 OpenSSL，要求 Python 3.10+、OpenSSL 3.x，无需安装 Python 第三方依赖。
`mtls.py` 管理 CA、客户端证书、P12 和 CRL，并输出文件的绝对路径。
Nginx 等验证端由你配置、同步公开材料和重载；应用自身负责登录与权限检查，服务端域名证书继续由现有 HTTPS 证书工具管理。

未保存默认目录时，数据目录为当前用户的 `~/.local/share/mtls-kit`；新 CA 默认名称为 `MTLS Client CA`。
`init` 成功后会记住数据目录，后续命令未指定 `--data-dir` 时自动使用它。
已有 CA 和证书可通过 `--data-dir 原数据目录` 继续管理，无需重新初始化。
新配置使用 `mtls_ca` 节；工具不会自动改写已有 CA 的名称或配置。
所有示例从 `mtls-kit` 项目目录执行，全局参数放在子命令之前。
初始化示例按场景选择一种，不要依次执行；已有 CA 直接使用查询、签发等命令，不再执行 `init`。

服务器直接部署 Nginx 时的证书签发、Nginx 手动配置和浏览器安装流程见 [mTLS 部署指南](docs/nginx-mtls.md)。
部署示例在服务器运行脚本，使用 `--data-dir /data/mtls-kit/data`；Nginx 从 `/data/mtls-kit/public` 读取公开 CA 证书和 CRL，CA 私钥目录不提供给业务应用。

```text
/data/mtls-kit/
├── mtls.py      # 证书管理脚本
├── data/        # 私有签发数据，仅管理员可访问
└── public/      # 公开 CA 证书和 CRL，供 Nginx 读取
```

## 默认数据目录

目录选择顺序为：命令行 `--data-dir` → 当前用户已保存的目录 → `~/.local/share/mtls-kit`。
成功初始化后，工具将绝对路径保存到 `~/.config/mtls-kit/config.json`，配置只包含路径：

```json
{
  "data_dir": "/data/mtls-kit/data"
}
```

```bash
# 服务器 root 用户首次初始化；已初始化时跳过这一行
python3 mtls.py --data-dir /data/mtls-kit/data init

# 后续自动使用该目录
python3 mtls.py status
python3 mtls.py issue
python3 mtls.py list
```

配置按运行用户分别保存，初始化和后续操作应使用同一用户。服务器示例按 root 直接执行，root 的配置通常位于 `/root/.config/mtls-kit/config.json`。
Linux 配置目录权限为 700，配置文件为 600；配置不包含密码、私钥或证书内容。
只有成功的 `init` 会更新默认目录，其他命令的 `--data-dir` 只用于本次操作。
若 CA 已初始化但配置保存失败，工具会明确提示继续使用 `--data-dir`；证书数据会保留，不要重复初始化。
配置损坏时会报错，不自动回退到其他目录；可以显式使用 `--data-dir` 继续操作并修复配置。
已有 CA 不要重新初始化：继续指定 `--data-dir`，或手动将其绝对路径填入上述配置文件。
该配置不会搬移证书数据或修改 `openssl.cnf` 中的目录。

## 快速开始

以下是本机首次使用的完整流程，与上面的服务器初始化示例二选一。已有 CA 时跳过 `init`。

```bash
python3 mtls.py status

# 初始化专用 CA，交互设置名称、密钥算法、有效期和 CA 密码
python3 mtls.py init

# 每台设备使用独立名称，交互选择算法、有效期并输入密码
python3 mtls.py issue admin-laptop

# 查看清单、证书详情及本地校验结果
python3 mtls.py list
python3 mtls.py show admin-laptop
python3 mtls.py check admin-laptop

# 输出 CA 证书和 CRL 路径；带设备名时也输出该设备证书、加密私钥和 P12 路径
python3 mtls.py paths
python3 mtls.py paths admin-laptop
```

`status` 是只读环境检查：显示 Python、OpenSSL、实际数据目录，以及是否存在 `openssl.cnf`。
“CA 已初始化”为真仅表示该配置文件存在，不代表 CA 材料完整、证书有效或线上验证正常；具体设备证书使用 `check 设备名` 校验。

`init` 按顺序询问以下设置，前三项直接回车使用默认值：

```text
CA 名称（默认 MTLS Client CA）：
CA 密钥算法：
  1. ECDSA P-256
  2. ECDSA P-384
  3. ECDSA P-521
  4. RSA 3072
  5. RSA 4096
请选择数字（默认 1）：
CA 有效期（天，正整数）（默认 3650）：
CA 私钥密码：
再次输入CA 私钥密码：
```

无效名称、选项或天数会提示重新输入。CA 名称支持中文，UTF-8 编码长度不超过 64 字节，不能包含控制字符或反斜杠。
CA 有效期须为正整数，工具不设置最大天数，直接回车仍使用 3650 天。
CA 密码仍须至少 8 个字符，不能为空；输入时不显示字符。CRL 初始有效期仍为 30 天。
`init --cn`、`--key-algorithm`、`--days` 可以直接设置对应项，已提供的项不再询问。
初始化时选择的算法仅用于 CA，客户端签发和续签仍默认 P-256。

`issue` 支持省略设备名，此时先询问设备名，再依次选择算法、有效期和输入密码：

```bash
python3 mtls.py issue
```

```text
设备名：
客户端密钥算法：
  1. ECDSA P-256
  2. ECDSA P-384
  3. ECDSA P-521
  4. RSA 3072
  5. RSA 4096
请选择数字（默认 1）：
客户端有效期（天，1～3650）（默认 1095）：
CA 私钥密码：
客户端证书密码：
再次输入客户端证书密码：
```

设备名必填，无默认值；仅允许 1～64 位英文字母、数字、点、下划线和短横线，首位须为字母或数字。
在命令行提供设备名、`--key-algorithm` 或 `--days` 时，不再询问对应项；客户端有效期不能超过 CA 剩余有效期。

签发和续签完成后自动打印所有相关文件的绝对路径。
将 `.p12` 导入系统或浏览器的个人证书库，输入客户端密码即可。
P12 包含证书、私钥和 CA 公钥证书；客户端 PEM 私钥本身也加密保存。
证书仅包含 `clientAuth` 用途，不能作为服务端 HTTPS 证书使用。
新初始化的 CA、签发和续签的客户端证书默认使用 ECDSA P-256 密钥（OpenSSL 曲线名 `prime256v1`）。
升级脚本不会更换已有 CA；现有 RSA CA 仍可签发 P-256 客户端证书。
服务端域名证书继续使用现有 ACME 工具管理。

## 续签与重新导出

```bash
# 生成新的私钥和证书，保留旧版本，便于安装切换
python3 mtls.py renew admin-laptop
python3 mtls.py list --name admin-laptop

# 重新导出，设置新的 P12 密码，不改变已有 PEM 私钥的密码
python3 mtls.py export admin-laptop --output ./admin-laptop-new.p12

# 安装并验证新证书后，吊销旧版本
python3 mtls.py revoke admin-laptop --serial 旧序列号 --reason superseded
```

每次签发保存在 `clients/设备名/序列号/`，不会覆盖旧证书或私钥。
已有客户端证书保持原状态，仍可检查、重新导出和吊销；执行 `renew` 默认生成 ECDSA P-256 新版本。
`renew` 仍须在命令行指定设备名，默认有效期 1095 天；可用 `--key-algorithm` 和 `--days` 指定新版本的算法和有效期。
安装并验证新 P12 后再吊销旧版本。现有 RSA CA 可继续使用，无需重新执行 `init` 或替换 Nginx 的 CA 证书。
`show`、`check`、`export`、`paths`、`revoke` 支持 `--serial 十六进制序列号`。
省略时选择该设备最新序列号；最新证书吊销后，不会自动退回旧的有效版本。
已经全部吊销的设备不可直接续签，重新授权时使用新的设备名。

## 吊销与 CRL

```bash
# 设备丢失时，吊销该设备全部版本，避免旧证书仍能使用
python3 mtls.py revoke admin-laptop --all --reason keyCompromise

# 定期刷新 CRL，即使近期没有新吊销也需要刷新
python3 mtls.py crl --days 30

# 被吊销证书校验失败，返回非零退出码
python3 mtls.py check admin-laptop
```

`revoke` 成功后会自动更新 CA 数据库和本地 `ca.crl`，新 CRL 默认从本次生成起有效 30 天。
可用 `revoke --crl-days` 指定本次 CRL 有效期，例如：

```bash
python3 mtls.py revoke admin-laptop --all --reason keyCompromise --crl-days 30
```

如果吊销记录已写入、CRL 生成失败，命令会返回非零退出码；修复错误后重复吊销可重试 CRL 生成。
`--all` 与 `--serial` 不能同时使用，不指定两者时只吊销最新版本。

**本地吊销不等于线上生效。** 根据输出路径，将新 `ca.crl` 手动同步到已有验证端，并按你的部署流程重新加载。
CRL 默认有效 30 天，到期前必须刷新并同步；脚本不创建定时任务、不重载服务。
`crl --days` 和 `revoke --crl-days` 都接受 1～3650 天，未指定时为 30 天。
已建立的 TLS 连接不会因为更新 CRL 立即中断，证书安装和线上验证仍需手动完成。

## 数据与安全处理

CA 默认有效期 3650 天（约 10 年），客户端证书默认 1095 天（约 3 年），签发和续签使用相同默认值。
可使用 `init --days`、`issue --days`、`renew --days` 调整，客户端有效期不能超过 CA 剩余有效期。
默认值变更不会延长已签发证书；已有设备需要执行 `renew` 并安装新 P12 才能使用新的有效期。
日期清单使用 UTC。

```text
数据目录/
├── ca.crt               # 交给验证端的 CA 公钥证书
├── ca.crl               # 交给验证端的吊销列表
├── private/ca.key       # 加密 CA 私钥，仅签发机持有
├── openssl.cnf          # 客户端签发策略
├── index.txt            # 签发与吊销记录
├── serial / crlnumber   # 证书与 CRL 编号
├── newcerts/            # OpenSSL 归档的已签发证书
└── clients/             # 按设备、序列号保存证书、加密私钥和 P12
```

- 数据目录不要放进 Git、Docker 构建上下文或公开网站目录。
- 备份整个数据目录，仅备份 CA 私钥不能恢复签发与吊销历史。
- 恢复备份时保持原数据路径；若迁移路径，需要同步修改 `openssl.cnf` 的 `dir`。
- 使用已保存的默认目录时，迁移后也需更新 `config.json` 的 `data_dir`，或显式指定新目录。
- CA 建议在管理员电脑或独立签发机管理，验证端只需 `ca.crt` 和 `ca.crl`。
- 新密码至少 8 个字符，密码不进入命令行或日志，仅通过子进程环境变量交给 OpenSSL。
- Linux 数据目录为 700。Windows 的 chmod 不设置 NTFS ACL，应使用仅当前用户可访问的目录。
- 重复初始化和覆盖已有 P12 输出都会被拒绝。
- 所有 CA 修改持有 `.lock`。异常退出后，确认进程结束，再手动删除遗留锁。
- 签发失败保留中间目录，先检查 `list` 和 `pending-*` 中的文件，避免丢失已签发证书的私钥。

## 自定义路径及非交互调用

下面的交互初始化与非交互初始化是两种替代方式，只选择一种。已有 CA 直接从签发或查询步骤开始。

```bash
# 方式一：交互初始化新目录
python3 mtls.py --data-dir /secure/mtls-kit init
python3 mtls.py --data-dir /secure/mtls-kit paths
```

```bash
# 方式二：非交互初始化新目录，与上面的方式一二选一
# 密码文件仅包含一行密码，Linux 权限必须为 600
# 需显式提供名称、密钥算法和有效期
python3 mtls.py --data-dir /secure/mtls-kit \
  --ca-password-file /secure/ca-password \
  init --cn "MTLS Client CA" --key-algorithm p256 --days 3650
```

初始化完成后再签发或导出。以下签发示例通过参数提供所有设置，无需终端输入：

```bash
# 非交互签发需显式提供设备名、算法和有效期
python3 mtls.py \
  --data-dir /secure/mtls-kit \
  --ca-password-file /secure/ca-password \
  issue admin-laptop --key-algorithm p256 --days 1095 \
  --client-password-file /secure/laptop-password

# 重新导出时可指定两个不同的密码文件
python3 mtls.py --data-dir /secure/mtls-kit export admin-laptop \
  --client-password-file /secure/laptop-password \
  --p12-password-file /secure/export-password \
  --output /secure/admin-laptop.p12
```

用 `--openssl /完整路径/openssl` 指定 OpenSSL。
Windows 示例：`python mtls.py --openssl "C:\Program Files\Git\usr\bin\openssl.exe" status`。
CA 数据目录不支持换行、双引号或美元符号，以避免 OpenSSL 配置语法歧义。

脚本在调用 OpenSSL 时使用 `MTLS_KIT_CA_PASSWORD`、`MTLS_KIT_CLIENT_PASSWORD` 和 `MTLS_KIT_P12_PASSWORD` 临时环境变量传递密码。
这些变量由脚本内部设置，无需手动导出；非交互调用使用上面的密码文件参数。

`--help` 或 `子命令 --help` 查看全部参数。
脚本不导入现有 CA，不自动更换 CA，不清除历史，不撤销已完成的吊销操作。

参考：[OpenSSL CA](https://docs.openssl.org/3.5/man1/openssl-ca/)、[PKCS#12](https://docs.openssl.org/3.5/man1/openssl-pkcs12/)。
