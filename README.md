# checkin-hub

Trae（国内版）和 WorkBuddy 每日自动签到，docker 常驻，不用开电脑。纯 Python 标准库，无第三方依赖，arm/x86 都能跑。

- WorkBuddy：每天 100 积分（连签有额外奖励）
- Trae：每天约 150 积分
- 每天北京时间 00:23（Trae）/ 00:30（WorkBuddy）起签，失败每 30 分钟重试，成功/放弃时推送 ntfy（可选）
- 轮换后的令牌自动保存在 `./data`，容器重启不丢

## 部署

镜像由 GitHub Actions 自动构建并推送到 `ghcr.io/lu8010252/checkin-hub:latest`，支持 `linux/amd64`、`linux/arm64`、`linux/arm/v7`，不用下载源码：

```bash
mkdir checkin-hub && cd checkin-hub
# 1. 把仓库里的 docker-compose.yml 和 .env.example 放进来
cp .env.example .env     # 2. 按下面「获取令牌」填好
docker compose up -d     # 3. 启动
docker compose logs -f
```

想自己构建：把 `docker-compose.yml` 里的 `build: .` 取消注释，用 `docker compose up -d --build`。

先试跑一次看结果（不影响常驻服务）：

```bash
docker compose run --rm checkin python app.py once
```

只填你要用的那个：没填令牌的任务不会启用。

## 获取令牌

### WorkBuddy（1 项）

电脑上登录着 WorkBuddy 客户端，打开登录态文件，找到 `auth.refreshToken`（约 700 字符，整串复制，别漏）：

- Windows：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`（找不到再看 `%APPDATA%` 同路径）
- macOS：`~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`

填入 `.env` 的 `WORKBUDDY_REFRESH_TOKEN`。如果文件读不了（官方近期加过限制），用 WorkBuddy 自己让它帮你读出这个字段。

### Trae（5 项）

Trae 续期需要「设备证明」，所以要从登录着 Trae 的那台电脑导出令牌和设备密钥。用 [L0NE-6/Trae-AutoCheckin](https://github.com/L0NE-6/Trae-AutoCheckin) 仓库里的 `trae_get_token.py`：

```bash
python trae_get_token.py                 # 打印 TRAE_REFRESH_TOKEN
python trae_get_token.py --export-keys   # 打印 TRAE_DEVICE_KEY_PEM / TRAE_DEVICE_PUB_PEM / TRAE_DEVICE_ID / TRAE_MACHINE_ID
```

把打印的 5 个变量原样填进 `.env`（PEM 保持一行，换行写成 `\n`，脚本导出的就是这种格式）。如果提示缺 `cryptography`，`pip install cryptography` 即可（只在导出这一步需要）。

## 注意

1. **Trae 的令牌只能在一处续期。** 服务器续期后，电脑上的 Trae 客户端登录态可能失效，需要重新登录；重新登录后，要重新导出一次填进 `.env`（改了 `.env` 里的令牌会自动重置缓存），再 `docker compose up -d` 重启。
2. 一个设备号一天只能签一个 Trae 账号，所以这里只支持单账号。
3. 一直提示 9074：多半是设备号不对（必须是客户端真实注册的那串数字），重新导出。
4. 这些是非官方接口，官方改接口或风控后可能失效；失效时会推送通知，日志里有原因。账号风险自负，建议别用主力号。
5. `.env` 和 `data/` 里是账号凭证，不要提交到公开仓库（已在 `.gitignore`）。

## 可选配置（.env）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `TRAE_TIME` / `WORKBUDDY_TIME` | 每天几点开始签（北京时间） | 00:23 / 00:30 |
| `RETRY_MIN` | 失败后多少分钟重试 | 30 |
| `MAX_TRIES` | 每天最多尝试次数 | 8 |
| `NTFY_URL` / `NTFY_TOKEN` | ntfy 通知地址 / 令牌 | 空（不推送） |
| `CHECKIN_PROXY` | 走代理访问 | 空（直连） |
| `TRAE_IDE_VERSION` | 模拟的 Trae 版本 | 1.107.1 |
