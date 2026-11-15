# checkin-hub 签到中心

Trae（国内版）和 WorkBuddy 每日自动签到，带网页后台，docker 常驻，不用开电脑。除二维码用的 `qrcode` 外全是 Python 标准库。

- WorkBuddy：每天 100 积分（连签有额外奖励）
- Trae：每天约 150 积分
- **网页后台**：总览（今日是否签到、积分/连签、近 14 天、最近记录）、立即签到、在网页里改令牌/时间/通知，不用再改文件重启
- **网页扫码登录**：令牌失效时在网页点一下生成二维码，手机扫码即可，令牌自动写入（WorkBuddy）
- **网页一键登录**：Trae 生成一个脚本，在电脑上双击就跑完登录并回传令牌（Trae 的回调只能落在本机 127.0.0.1，没法手机扫码）
- 每天北京时间 00:23（Trae）/ 00:30（WorkBuddy）起签，失败自动重试，成功/失败/放弃时推送 ntfy（可选）
- 轮换后的令牌自动保存在 `./data`，容器重启不丢
- 镜像由 GitHub Actions 构建：`linux/amd64`、`linux/arm64`、`linux/arm/v7`

## 部署

```bash
mkdir checkin-hub && cd checkin-hub
# 把仓库里的 docker-compose.yml 放进来，改掉里面的 WEB_PASSWORD
docker compose up -d
```

浏览器打开 `http://服务器IP:8090`，用 `WEB_PASSWORD` 登录，到「设置」里填令牌即可。

> 没设置 `WEB_PASSWORD` 时会生成随机密码，写在日志（`docker compose logs`）和 `data/.web_password` 里。
> **别把后台直接暴露公网**：用反向代理加 HTTPS（并设 `TRUST_PROXY=1`），或只在内网访问。

想自己构建：取消 `docker-compose.yml` 里 `build: .` 的注释，用 `docker compose up -d --build`。

试跑一次看结果（不影响常驻服务）：

```bash
docker compose exec checkin python app.py once
```

## 登录 / 获取令牌

令牌在网页「设置」里改，改完今天还没签成的会立即重试。下面两种是推荐做法，末尾保留手动导出的备用方案。

### WorkBuddy：网页扫码

后台导航「扫码登录」→ 点「生成二维码」→ 手机扫码登录。二维码里的 `state` 是一次性的（30 分钟有效），后台会轮询到令牌自动写入，并立刻重签一次。

手动填的话：电脑上登录着 WorkBuddy 客户端，打开登录态文件，找到 `auth.refreshToken`（约 700 字符，整串复制，别漏）：

- Windows：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`（找不到再看 `%APPDATA%` 同路径）
- macOS：`~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`

### Trae：网页一键登录

后台导航「Trae 登录」→ 生成登录脚本 → 下载后在自己电脑上双击（需要 Python 3）：

1. 脚本在本机 `127.0.0.1:8931` 起回调服务（Trae 授权页只认这个地址，改成局域网地址会直接报「网络错误」）
2. 自动打开 Trae 登录页，完成登录
3. 脚本把授权码转给后台，后台用自己的设备身份换令牌、写进配置，然后立即重签

脚本 30 分钟内有效，过期回网页重新生成即可。

手动导出 5 项（`TRAE_REFRESH_TOKEN` / `TRAE_DEVICE_ID` / `TRAE_MACHINE_ID` / `TRAE_DEVICE_KEY_PEM` / `TRAE_DEVICE_PUB_PEM`）：Trae 续期需要「设备证明」，所以要从登录着 Trae 的那台电脑导出，用 [L0NE-6/Trae-AutoCheckin](https://github.com/L0NE-6/Trae-AutoCheckin) 里的 `trae_get_token.py`：

```bash
python trae_get_token.py                 # 打印 TRAE_REFRESH_TOKEN
python trae_get_token.py --export-keys   # 打印 TRAE_DEVICE_KEY_PEM / TRAE_DEVICE_PUB_PEM / TRAE_DEVICE_ID / TRAE_MACHINE_ID
```

PEM 可以直接粘贴多行，也可以是一行里用 `\n` 代替换行。输出里有令牌和设备私钥，别发给别人、别贴到公开的地方。

## 注意

1. **Trae 的令牌只能在一处续期。** 服务器续期后，电脑上的 Trae 客户端登录态可能失效，需要重新登录；重新登录后回网页点一次「Trae 登录」跑脚本即可。改了令牌后，今天没签成的会立即重试。
2. 一个设备号一天只能签一个 Trae 账号，所以这里只支持单账号。
3. 一直提示 9074：多半是设备号不对（必须是客户端真实注册的那串数字），重新导出。
4. 这些是非官方接口，官方改接口或风控后可能失效；失效时会推送通知，网页和日志里有原因。账号风险自负，建议别用主力号。
5. 令牌只写不读：网页永远不显示完整令牌，只显示前后几位。`data/` 里是账号凭证，不要提交到公开仓库（已在 `.gitignore`）。

## 环境变量（都可选，网页里保存的设置优先）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `WEB_PASSWORD` | 网页后台登录密码 | 随机生成 |
| `WEB_PORT` | 容器内网页端口 | 8080 |
| `TRUST_PROXY` | 前面有反向代理时设 `1`（读取真实 IP、HTTPS cookie） | 空 |
| `CHECKIN_PROXY` | 访问 Trae/WorkBuddy 接口走的代理（仅环境变量可设） | 空（直连） |
| `TRAE_IDE_VERSION` | 模拟的 Trae 版本 | 1.107.1 |

其余（令牌、时间、重试、ntfy）都在网页「设置」里改。
