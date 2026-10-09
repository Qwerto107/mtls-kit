# mtls-kit 与服务器 Nginx 部署指南

本文使用独立域名保护一个业务应用。浏览器手动安装客户端证书，服务器 Nginx 验证证书，并将通过验证的请求转发给应用。
示例应用监听 `127.0.0.1:8080`；请按实际部署替换域名、服务地址和端口，应用继续执行自己的登录和权限检查。

证书工具只生成、管理证书并输出路径。下面的 Nginx 和应用配置由你手动合并到现有部署；工具不会修改配置或重载服务。
脚本的完整命令说明见 [证书管理工具](../README.md)。
服务器端命令统一在 root shell 中直接执行；浏览器证书安装和客户端 curl 验证在对应设备上完成。
文中的初始化命令是替代示例，每套 CA 只初始化一次；已有 CA 跳过所有 `init`，沿用原证书数据。

## 1. 部署约定

| 项目 | 本文示例 |
| --- | --- |
| 受保护域名 | `admin.example.com`，直接访问网关 |
| TLS 网关服务 | 服务器直接安装的 Nginx，示例由 systemd 管理 |
| 受保护应用 | `127.0.0.1:8080` |
| 证书工具运行方式 | 从 GitHub 获取或手动安装脚本，在服务器运行 |
| CA 数据目录 | `/data/mtls-kit/data` |
| 脚本路径 | `/data/mtls-kit/mtls.py` |
| Nginx 公开验证材料 | `/data/mtls-kit/public` |
| 495 证书校验失败页面 | `/usr/share/nginx/mtls-errors/mtls-error.html` |
| 496 未提供证书页面 | `/usr/share/nginx/mtls-errors/mtls-missing.html` |

`/data/mtls-kit/public` 只存放公开 CA 证书和 CRL，不存放私钥或 P12。Nginx 需有父目录的进入权限和公开材料的读取权限；私钥仍保存在仅 root 可读的 `/data/mtls-kit/data`。

请求路径为：浏览器 → 服务器 Nginx（mTLS）→ 本机受保护应用。

本文假设 Nginx 和应用部署在同一台 Linux 服务器，应用可通过上述本机端口访问。
应用可以直接运行，也可以用 Docker 并将端口发布到服务器的 `127.0.0.1`；Nginx 不使用容器内服务名解析上游。
端口、目录、域名和服务管理方式请按实际部署替换。如果后端在其他服务器，改用实际内网地址，并限制只允许 Nginx 服务器访问。
受保护域名前面若有 CDN，必须另行确认客户端证书由谁验证；源站收到的可能只是 CDN 的连接，不能直接套用本文。

### 文件读取漏洞与证书隔离

客户端公钥证书、CA 公钥证书和 CRL 不包含私钥，读取这些文件不能冒充客户端。
需要保护的是 CA 私钥、客户端私钥和包含私钥的 P12；即使文件加密，也不应把它们暴露给业务应用的文件读取范围。

本方案在服务器 `/data/mtls-kit/data` 保存全部签发数据，仅 root 可读；Nginx 从单独的 `/data/mtls-kit/public` 读取公开 CA 证书与 CRL。
应用直接运行时，应使用独立的非 root 用户，不能让该用户读取签发数据。应用使用 Docker 时，不挂载签发目录，也不能把包含签发数据的 `/data/mtls-kit`、其父目录或服务器根目录整体挂载给应用。
这种隔离缩小文件读取漏洞能访问的范围，漏洞本身仍应修复。

## 2. 把工具复制到服务器运行

服务器需要 Python 3.10+、OpenSSL 3.x，无需安装业务应用依赖或 Python 第三方包。
先检查版本；缺少时使用服务器发行版的包管理器安装，例如 Debian/Ubuntu 的 `apt install python3 openssl`。

```bash
python3 --version
openssl version

# 签发数据只允许 root 访问，不挂载给业务应用。
install -d -o root -g root -m 755 /data/mtls-kit
install -d -o root -g root -m 700 /data/mtls-kit/data

# 网关只读取公开验证材料和错误页面。
install -d -o root -g root -m 755 /data/mtls-kit/public /usr/share/nginx/mtls-errors
```

选择以下一种方式安装脚本。

**从 GitHub 获取：** 使用 curl 直接保存脚本：

```bash
curl --fail --location --show-error \
  --output /data/mtls-kit/mtls.py \
  https://raw.githubusercontent.com/Qwerto107/mtls-kit/main/mtls.py
```

**手动安装：** 从 [GitHub 仓库](https://github.com/Qwerto107/mtls-kit) 下载源码或脚本，或把可信的本地脚本上传到服务器。在 `mtls.py` 所在目录执行：

```bash
# 核对脚本来源后安装，只有 root 能修改和读取。
install -o root -g root -m 600 \
  mtls.py /data/mtls-kit/mtls.py
```

在 Bash 终端定义快捷函数，所有签发操作都直接在服务器执行：

```bash
mtls() {
  # 使用当前用户保存的目录，并原样转发命令参数。
  python3 /data/mtls-kit/mtls.py "$@"
}

mtls status
# 仅首次初始化指定目录时执行；已有 CA 跳过这一行
mtls --data-dir /data/mtls-kit/data init
mtls issue admin-laptop
mtls paths admin-laptop
```

`mtls` 是当前终端的 Bash 函数，新开终端需重新定义，或保存到当前用户的 `~/.bashrc` 后重新加载。
快捷函数不固定数据目录，使用已保存的默认目录；首次初始化时可按示例显式指定 `/data/mtls-kit/data`，或执行 `mtls init` 并在目录提示中输入该路径，后续无需重复传入。
需要 Tab 补全时，在 Bash 4+ 中定义函数后加载：

```bash
source <(mtls completion bash)
```

补全支持子命令、参数、算法、吊销原因、已有设备名和路径，不调用 OpenSSL、不询问密码。
设备名使用当前用户已保存的目录或命令行中的 `--data-dir`。
更新脚本后重新加载补全；详细说明见 [Bash Tab 补全](../README.md#tab-补全)。

如需每次打开 Bash 终端都启用，编辑当前用户的 `~/.bashrc`，将以下完整配置加入文件末尾。
本文使用 root 操作，对应文件为 `/root/.bashrc`；已存在的 `mtls` 函数和补全加载行应替换，避免重复添加。需要 Bash 4+：

```bash
# mtls-kit 快捷命令：使用当前用户保存的数据目录。
mtls() {
  python3 /data/mtls-kit/mtls.py "$@"
}

# 先定义函数，再加载 Tab 补全。
source <(mtls completion bash)
```

保存后在当前 Bash 终端重新加载，并查看状态：

```bash
source ~/.bashrc
mtls status
```

`mtls status` 显示 mtls-kit 工具版本、Python 和 OpenSSL 版本、实际数据目录及 `openssl.cnf` 是否存在，不证明 CA 材料完整；设备证书用 `mtls check 设备名` 校验。
只查看工具版本可用 `mtls -V`，无需读取 CA 配置或调用 OpenSSL。常用参数支持短写，例如 `-d` 为 `--data-dir`、`-o` 为 `--output`；完整对应表见 [常用短参数与版本](../README.md#常用短参数与版本)。
不使用函数时，首次初始化也可直接执行下面的等价命令，与上面的快捷函数初始化命令二选一：

```bash
python3 /data/mtls-kit/mtls.py --data-dir /data/mtls-kit/data init
```

初始化成功后，数据目录会保存到当前运行用户的 `~/.config/mtls-kit/config.json`；本文由 root 直接操作，配置通常属于 root 并位于 `/root/.config/mtls-kit/config.json`。
后续可直接执行以下命令，自动使用保存的目录：

```bash
python3 /data/mtls-kit/mtls.py status
python3 /data/mtls-kit/mtls.py issue
```

显式 `--data-dir` 始终优先；普通用户与 root 各自使用自己的配置。只有成功初始化会保存目录，签发、查询等命令不会更改默认目录。
已有 CA 不要重新执行 `init`：继续使用原路径参数，或将其绝对路径手动写入当前用户配置文件的 `data_dir`。

每套 CA 只执行一次 `init`，已有数据不会被覆盖。未指定 `--data-dir` 时，初始化先询问 CA 数据目录：回车使用已保存的目录，首次使用 root 用户时默认为 `/root/.local/share/mtls-kit`；本文部署应输入 `/data/mtls-kit/data`。
显式指定 `--data-dir /data/mtls-kit/data` 时跳过目录提示；目录必须不存在或为空，交互输入无效时会要求重新输入。
随后依次询问 CA 名称、密钥算法和有效期，直接回车分别使用 `MTLS Client CA`、ECDSA P-256 和 3650 天（约 10 年），最后输入并确认 CA 私钥密码。
密钥算法用数字选择：1 为 P-256、2 为 P-384、3 为 P-521、4 为 RSA 3072、5 为 RSA 4096；CA 有效期可输入任意正整数天数，工具不设置最大天数。
CA 和客户端默认使用 ECDSA P-256（OpenSSL 曲线名 `prime256v1`），客户端证书默认有效期 1095 天（约 3 年）；初始化选择其他 CA 算法不会改变客户端默认值。
升级脚本不会更换已有 CA；现有 RSA CA 可继续签发 P-256 客户端证书，无需重新初始化。
签发和续签均可通过 `--days` 自定义有效期，但不能超过 CA 剩余有效期。已有证书的到期日不会随脚本升级改变，需要续签并安装新 P12。
`mtls issue` 省略设备名时会询问设备名；未提供 `--key-algorithm`、`--days` 时会交互选择客户端算法、输入有效期，直接回车使用 P-256 和 1095 天。
非交互签发可用 `mtls issue admin-laptop --key-algorithm p256 --days 1095`，同时按工具说明使用密码文件。
新签发的 P12 导入密码与该版本加密 PEM 私钥密码相同。
升级脚本时更新 `/data/mtls-kit/mtls.py`，不要删除签发数据或重新初始化。

### 输出路径与文件用途

工具直接输出服务器绝对路径。假设设备序列号为 `<SERIAL>`：

| 文件路径 | 用途 |
| --- | --- |
| `/data/mtls-kit/data/ca.crt` | 交给网关的客户端 CA 公钥证书 |
| `/data/mtls-kit/data/ca.crl` | 交给网关的吊销列表 |
| `/data/mtls-kit/data/private/ca.key` | 加密 CA 私钥，仅签发机持有 |
| `/data/mtls-kit/data/clients/admin-laptop/<SERIAL>/client.crt` | 客户端公开证书 |
| `/data/mtls-kit/data/clients/admin-laptop/<SERIAL>/client.key` | 客户端加密私钥 |
| `/data/mtls-kit/data/clients/admin-laptop/<SERIAL>/client.p12` | 浏览器安装包，包含客户端私钥 |

重新导出到服务器目录：

```bash
mtls export admin-laptop \
  -o /data/mtls-kit/data/exports/admin-laptop.p12
```

已有文件不会被覆盖。初始化后保持数据路径稳定，因为 `openssl.cnf` 记录 CA 数据的绝对目录。
本工具不会自动迁移已有证书数据。已有 CA 可以继续保留原目录，将上面的 `--data-dir` 换为原路径即可。
若要迁移到 `/data/mtls-kit/data`，先备份整个目录，停止签发和吊销操作，将完整数据复制到新目录并保持目录 700、私钥 600，再把副本 `openssl.cnf` 的 `dir` 改为 `/data/mtls-kit/data`。
随后检查 `mtls list`、`mtls check admin-laptop`，确认无误后再更新公开材料路径；保留原目录备份，不要重新执行 `init`，也不需要改已有 CA 名称或配置节名称。
如果后续依赖已保存的默认目录，还需将当前用户配置文件的 `data_dir` 更新为新目录；配置只记录路径，不会自动迁移数据。

## 3. 同步公开材料

在服务器执行以下命令，将公开材料复制到 Nginx 专用目录：

```bash
install -o root -g root -m 644 /data/mtls-kit/data/ca.crt /data/mtls-kit/public/ca.crt
install -o root -g root -m 644 /data/mtls-kit/data/ca.crl /data/mtls-kit/public/ca.crl
```

## 4. 服务器 Nginx 手动配置

先按现有 ACME 流程取得 `admin.example.com` 的服务端证书。下面假设证书由服务器上的 Certbot 管理。
如果使用其他 ACME 客户端，把 `ssl_certificate` 和 `ssl_certificate_key` 路径替换为其实际产物。

将以下示例合并到已有受保护域名的 `server` 配置；新建配置时可保存为 `/etc/nginx/conf.d/mtls-admin.conf`，确认主配置包含 `/etc/nginx/conf.d/*.conf`。
使用 `sites-available` / `sites-enabled` 或其他配置目录时，保存到实际会被加载的位置，避免为同一域名重复定义 `server`。
Nginx 直接读取服务器上的证书和页面文件，无需 Docker 挂载。

```nginx
server {
    listen 443 ssl;
    server_name admin.example.com;

    # 服务端仍使用公开可信的域名证书。
    ssl_certificate /etc/letsencrypt/live/admin.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/admin.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # 专用客户端 CA 和吊销列表。
    ssl_client_certificate /data/mtls-kit/public/ca.crt;
    ssl_crl /data/mtls-kit/public/ca.crl;
    ssl_verify_client on;
    ssl_verify_depth 1;

    # 本例关闭会话恢复，让新连接重新验证证书。
    ssl_session_cache off;
    ssl_session_tickets off;
    keepalive_timeout 15s;

    # 区分证书校验失败与未提供证书，对外均返回 HTTP 403。
    error_page 495 =403 /__mtls_error.html;
    error_page 496 =403 /__mtls_missing.html;
    location = /__mtls_error.html {
        internal;
        alias /usr/share/nginx/mtls-errors/mtls-error.html;
        charset utf-8;
        add_header Cache-Control "no-store" always;
    }
    location = /__mtls_missing.html {
        internal;
        alias /usr/share/nginx/mtls-errors/mtls-missing.html;
        charset utf-8;
        add_header Cache-Control "no-store" always;
    }

    # 当前网关直接接收公网请求，覆盖来访者提供的代理头。
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 将通过 mTLS 验证的请求交给业务应用，保留原始请求路径。
    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
```

`ssl_verify_client` 放在 `server` 层，本例整个受保护域名都需要客户端证书。
Nginx 的 `495` 表示证书校验失败，`496` 表示未提供必需证书；这里分别显示对应页面，对外都返回 HTTP 403。
若 TLS 握手本身被中断，浏览器可能显示自己的连接错误，无法保证所有失败都显示自定义页。[Nginx 错误处理说明](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#errors)

两份静态页面均包含中文与英文提示。495 页面保留原路径，在服务器 `/usr/share/nginx/mtls-errors/mtls-error.html` 保存：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>客户端证书未通过验证 / Client certificate rejected</title>
  <style>
    body { max-width: 560px; margin: 15vh auto; padding: 24px;
           font-family: sans-serif; line-height: 1.8; }
  </style>
</head>
<body>
  <!-- 两种语言均直接写入页面，无需加载外部资源。 -->
  <h1>客户端证书未通过验证</h1>
  <p>根据安全策略，您的请求已被拦截。本站已启用 mTLS 身份验证，您的客户端证书未通过验证，证书可能已过期、被吊销或由不受信任的 CA 签发。</p>
  <hr>
  <section lang="en">
    <h2>Client certificate rejected</h2>
    <p>Your request has been blocked by the security policy. This site requires mTLS authentication. Your client certificate could not be verified. It may have expired, been revoked, or been issued by an untrusted CA.</p>
  </section>
</body>
</html>
```

496 页面在服务器 `/usr/share/nginx/mtls-errors/mtls-missing.html` 保存：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>未提供客户端证书 / Client certificate required</title>
  <style>
    body { max-width: 560px; margin: 15vh auto; padding: 24px;
           font-family: sans-serif; line-height: 1.8; }
  </style>
</head>
<body>
  <!-- 两种语言均直接写入页面，无需加载外部资源。 -->
  <h1>未提供客户端证书</h1>
  <p>根据安全策略，您的请求已被拦截。本站已启用 mTLS 身份验证，您的浏览器未提供客户端证书。请安装有效的客户端证书，并在访问时选择该证书。</p>
  <hr>
  <section lang="en">
    <h2>Client certificate required</h2>
    <p>Your request has been blocked by the security policy. This site requires mTLS authentication. Your browser did not provide a client certificate. Please install a valid client certificate and select it when visiting this site.</p>
  </section>
</body>
</html>
```

两份文件都需可被 Nginx 工作进程读取，例如权限 644；目录需有读取和进入权限。
保存后在 root shell 设置权限：

```bash
chmod 644 /usr/share/nginx/mtls-errors/mtls-error.html /usr/share/nginx/mtls-errors/mtls-missing.html
```

页面使用内嵌样式，无需加载受证书验证保护的其他资源。

服务器上的 Nginx 已运行时，先检查后重载：

```bash
nginx -t
```

只有检查通过，再执行 `systemctl reload nginx`。
如果 Nginx 不由 systemd 管理，使用现有服务管理方式；直接运行的 Nginx 可用 `nginx -s reload`。[Nginx 官方重载说明](https://nginx.org/en/docs/control.html#reconfiguration)

首次启动 Nginx 时，也先检查配置：

```bash
nginx -t
```

只有检查通过，再执行 `systemctl start nginx`。

启动失败时，检查服务端证书、CA、CRL、页面和配置文件是否已存在，以及 443 端口是否被其他服务占用。

## 5. 限制绕过入口

受保护应用只监听 `127.0.0.1:8080`，避免客户端直接访问应用端口绕过 Nginx。
应用若运行在容器中，将实际服务端口发布到服务器回环地址，并确认没有同端口的公网映射。
后端部署在其他服务器时，使用实际内网地址，并限制只允许 Nginx 服务器访问。

检查其他域名、反代配置和端口，确保受保护应用不存在不要求客户端证书的访问入口。
应用的登录、用户权限和会话检查继续由应用自身执行；通过 mTLS 验证只表示客户端证书被接受。
应用需要使用代理头时，只信任实际 Nginx 来源，避免直接接受来访者伪造的代理信息。

## 6. 客户端手动安装证书

完成 Nginx 配置并启动或重载服务、检查绕过入口后，在对应客户端设备安装证书。

通过可信方式把对应设备的 `client.p12` 交给持有者。该文件包含私钥，不放进公开下载目录。

- Windows：打开 P12/PFX，导入“当前用户”的“个人”证书库；也可在浏览器证书设置中导入。
- Firefox：在证书管理的“您的证书”中导入，具体入口随版本变化。
- macOS：通过钥匙串访问导入个人证书，然后在浏览器中选择该证书。

输入签发时的客户端密码，重新打开应用页面。浏览器可能要求选择证书。
服务端使用公开可信证书时，不需要为了访问网站把私有客户端 CA 设为系统受信任网站根证书。

### Android 安装 P12

将该设备的 `client.p12` 通过可信方式传到手机，在系统设置中搜索“安装证书”或“凭据”。
常见入口为“安全与隐私 → 更多安全设置 → 加密与凭据 → 安装证书”，具体名称随 Android 版本和厂商变化。
选择“VPN 和应用”一类的用户证书用途，打开 P12，输入客户端密码并为证书命名；若系统要求，先设置屏幕锁。
这里导入的是带私钥的客户端身份，不能用“CA 证书”或仅供 Wi-Fi 使用的安装项代替。
安装后在 Chrome 访问受保护域名，并在系统证书选择提示中选择该设备的证书；可在“用户凭据”中核对安装结果。
系统凭据入口可参考 [Android 证书管理说明](https://support.google.com/pixelphone/answer/2844832?hl=en)，其页面中的 Wi-Fi 示例应按本工具用途改选“VPN 和应用”。

### Chrome 已安装证书但没有再次询问

先确认安装了对应的 P12，证书尚未过期，且具有可用私钥。Windows 下应位于当前用户的“个人”证书库；Android 下应作为“VPN 和应用”用户证书安装。
如果此前取消过证书选择，Chrome 可能继续沿用“不发送证书”的选择；Chromium 会按服务器主机和端口缓存这类决定。[Chromium 客户端证书选择缓存](https://github.com/chromium/chromium/blob/main/net/ssl/ssl_client_auth_cache.h)
可完全退出 Chrome 后重新打开再试。Windows 下确认后台 Chrome 进程也已结束，Android 下可在应用设置中强行停止后重新打开；仅刷新页面或关闭当前标签不一定建立新的认证流程。
若仍未出现选择提示，继续检查是否访问了正确受保护域名、是否直接连接 Nginx，以及 Nginx 的客户端 CA 和证书有效期是否匹配。

## 7. 验证部署

先在签发工具中检查本地证书与 CRL：

```bash
mtls check admin-laptop
```

以下 curl 示例在有客户端 P12 的 Linux 电脑执行，适用于 OpenSSL 后端的 curl。
命令不写密码，curl 会在终端询问证书密码。[curl 客户端证书参数](https://curl.se/docs/manpage.html#-E)

```bash
# 不携带证书，预期 HTTP 403 和自定义错误页。
curl -i https://admin.example.com/

# 将路径替换为本机 P12，通过 mTLS 后由应用返回业务响应。
curl -i --cert-type P12 --cert ./admin-laptop.p12 \
  https://admin.example.com/
```

Windows 系统 curl 如果使用 Schannel，先导入系统证书库并按其证书库语法访问，或直接用浏览器验证。

| 验证场景 | 预期 |
| --- | --- |
| 未提供证书或选择时取消 | 进入 496 页面，显示中英文证书安装提示；TLS 握手中断时可能显示浏览器连接错误 |
| 提供了无法通过验证的证书 | 进入 495 页面，显示中英文校验失败提示；TLS 握手中断时可能显示浏览器连接错误 |
| 有效证书访问应用 | 通过 mTLS 验证，请求进入应用；响应由应用自身规则决定 |
| 已吊销证书，最新 CRL 已加载，新建连接 | 拒绝访问 |
| 通过其他域名或应用端口绕过 mTLS | 不存在可直接访问受保护应用的入口 |

使用浏览器访问应用，检查客户端证书选择及应用自身的登录和权限流程。
测试时保持正常的服务端证书验证，不用 `curl -k` 掩盖域名或证书链问题。

## 8. 续签、吊销及同步 CRL

### 客户端续签

```bash
mtls renew admin-laptop
mtls list --name admin-laptop
mtls paths admin-laptop
```

安装新 P12，重新建立连接并验证成功，再吊销旧序列号：

```bash
mtls revoke admin-laptop \
  --serial 旧序列号 --reason superseded
```

续签使用同一个 CA，新证书无需更新 Nginx 的 CA 配置；吊销旧证书后需要同步新 CRL。
已有客户端证书在续签时默认生成 ECDSA P-256 新版本，可用 `--key-algorithm` 指定其他算法；旧版本保持原状态。在对应设备上重新导入新 P12 并验证访问成功后，再吊销旧版本。

### 设备丢失

```bash
# 一次吊销该设备全部版本，防止旧证书仍然有效。
mtls revoke admin-laptop --all --reason keyCompromise
```

`revoke` 成功后会自动生成新的本地 `ca.crl`，默认有效期 30 天；无需额外执行 `mtls crl` 才能完成这次本地更新。
可通过 `--crl-days` 调整本次 CRL 有效期，范围为 1～3650 天，例如 `mtls revoke admin-laptop --all --reason keyCompromise --crl-days 30`。
如果吊销记录已写入但 CRL 生成失败，修复错误后重复执行吊销命令可重试；线上仍需同步公开 CRL 并检查、重载 Nginx。

### 定期刷新 CRL

```bash
mtls crl --days 30
```

默认 CRL 有效 30 天。建议每周检查并刷新，刷新后同步到网关；这里不自动创建定时任务。
CRL 到期会影响正常客户端验证，不能只在发生吊销时才更新。

每次吊销或刷新后，在服务器更新公开副本并重载 Nginx：

```bash
# 同目录临时文件再改名，避免网关读取写入到一半的 CRL。
install -o root -g root -m 644 /data/mtls-kit/data/ca.crl /data/mtls-kit/public/ca.crl.new
openssl crl -in /data/mtls-kit/public/ca.crl.new \
  -CAfile /data/mtls-kit/public/ca.crt -verify -noout
```

只有签名验证成功才继续：

```bash
cp /data/mtls-kit/public/ca.crl /data/mtls-kit/public/ca.crl.previous
mv /data/mtls-kit/public/ca.crl.new /data/mtls-kit/public/ca.crl
nginx -t
```

只有 `nginx -t` 成功才执行 `systemctl reload nginx`（非 systemd 部署使用现有重载方式），再用新连接检查有效与已吊销证书。
检查失败时，保留新 CRL 修复配置；如需临时恢复上一份 CRL，应明确它可能重新接受刚吊销的证书，不要把回退当作吊销成功。
重载不会立即中断已建立的 TLS 连接，活动连接可能继续使用；确认吊销效果时关闭旧连接或重新启动浏览器。

服务端公开证书仍由原 ACME 工具续期；成功后按既有流程重载网关。
HTTP-01 续期需要不要求客户端证书的 challenge 入口，可单独在 80 端口提供；DNS-01 不依赖这个 HTTP 入口。[Certbot 续期说明](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)

## 9. 故障定位与备份

| 现象 | 优先检查 |
| --- | --- |
| 工具提示目录或文件找不到 | 确认使用服务器脚本，`--data-dir` 与 `openssl.cnf` 的 `dir` 都为 `/data/mtls-kit/data` |
| 重建应用容器后证书状态变化 | 检查是否误在容器内运行工具；按本文方式，签发状态保存在服务器上，不随应用重建改变 |
| Nginx 无法启动 | 服务器上的证书文件、符号链接目标、配置加载位置和权限是否正确 |
| 已安装证书仍然 403 | 浏览器是否选对证书；CA 是否一致；证书/CRL 是否过期或已吊销 |
| Chrome 取消选择后不再询问 | 确认 P12 含私钥，完全退出浏览器及后台进程后重试；仍异常时检查受保护域名和 Nginx 客户端 CA |
| 自定义页面显示 404/403 | 错误页面和目录是否允许 Nginx 工作进程读取 |
| 应用返回 502 | 服务器能否访问示例上游 `127.0.0.1:8080`；应用是否启动，实际监听地址和端口是否与反代一致 |
| 本地已吊销，线上仍能访问 | 公开 CRL 是否更新、网关是否重载、是否仍在复用旧连接 |
| 工具提示 CA 被锁定 | 确认上次进程已经退出后再清理 `.lock`，不要并发操作索引 |

查看网关日志和工具状态：

```bash
journalctl -u nginx -n 100 --no-pager
# 请求错误通常记录在 error_log 指定的文件，按实际路径替换。
tail -n 100 /var/log/nginx/error.log
mtls list
mtls show admin-laptop
```

备份服务器 `/data/mtls-kit/data` 整个目录，包括加密 CA 私钥、索引、序列号、客户端历史及 CRL。
在没有签发/吊销操作时备份，备份文件同样只允许管理员访问。
恢复时保持服务器数据路径 `/data/mtls-kit/data` 一致，不重新初始化 CA。
CA 私钥丢失无法继续签发或生成新的 CRL；CA 私钥泄露需要规划更换 CA，并重新签发客户端证书。
