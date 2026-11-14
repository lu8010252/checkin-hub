"""网页后台：纯标准库，不依赖任何第三方包。

页面：/ 总览（状态、历史、立即签到）  /settings 设置（令牌、时间、通知）
安全：密码登录 + 签名 cookie（HttpOnly/SameSite=Strict）+ CSRF 令牌 + 登录失败限速
      + CSP（页面不含任何脚本）。令牌只写不读：页面永远不回显完整令牌。
"""
import hashlib
import hmac
import html
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core
import p256
from common import (DATA_DIR, bj_date, bj_now, env, load_json, log, save_config,
                    save_json)

COOKIE = "ch_session"
SESSION_DAYS = 30
MAX_BODY = 64 * 1024
FAIL_LIMIT, FAIL_WINDOW = 5, 600

# ---------------- 密钥与密码 ----------------
_SECRET_FILE = os.path.join(DATA_DIR, ".web_secret")
_PW_FILE = os.path.join(DATA_DIR, ".web_password")


def _read_or_create(path, make):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            v = fh.read().strip()
        if v:
            return v
    except OSError:
        pass
    v = make()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(v)
    except OSError as e:
        log("⚠️ 无法保存 %s: %s" % (os.path.basename(path), e))
    return v


SECRET = _read_or_create(_SECRET_FILE, lambda: secrets.token_hex(32)).encode()


def password():
    pw = env("WEB_PASSWORD")
    if pw:
        return pw
    return _read_or_create(_PW_FILE, lambda: secrets.token_urlsafe(9))


def _sign(msg):
    return hmac.new(SECRET, msg.encode(), hashlib.sha256).hexdigest()


def make_session():
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    return "%s.%s" % (exp, _sign("s|" + exp))


def valid_session(val):
    try:
        exp, sig = val.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign("s|" + exp))
    except Exception:  # noqa: BLE001
        return False


def csrf_token(val):
    return _sign("c|" + (val or ""))[:32]


# ---------------- 登录失败限速 ----------------
_fails, _fail_lock = {}, threading.Lock()


def _blocked(ip):
    with _fail_lock:
        now = time.time()
        _fails[ip] = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        return len(_fails[ip]) >= FAIL_LIMIT


def _fail(ip):
    with _fail_lock:
        _fails.setdefault(ip, []).append(time.time())


# ---------------- HTML ----------------
E = html.escape

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--fg:#1c1f26;--mut:#6b7280;--line:#e5e7eb;--ac:#2563eb;--ok:#15803d;--okb:#dcfce7;
--warn:#b45309;--warnb:#fef3c7;--bad:#b91c1c;--badb:#fee2e2;--gray:#4b5563;--grayb:#e5e7eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b22;--fg:#e6e8ee;--mut:#9aa3b2;--line:#2a2f3a;--ac:#6ea0ff;
--ok:#4ade80;--okb:#12301d;--warn:#fbbf24;--warnb:#3a2c0b;--bad:#f87171;--badb:#3b1414;--gray:#aab2c0;--grayb:#262b35}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:16px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 16px}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 10px}
nav{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
nav a,nav button{color:var(--fg);text-decoration:none;padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);font:inherit;cursor:pointer}
nav a.on{border-color:var(--ac);color:var(--ac)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.grid{display:grid;gap:14px;grid-template-columns:1fr 1fr}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.pill{display:inline-block;white-space:nowrap;padding:2px 10px;border-radius:99px;font-size:13px;font-weight:600}
.p-ok{background:var(--okb);color:var(--ok)}.p-warn{background:var(--warnb);color:var(--warn)}
.p-bad{background:var(--badb);color:var(--bad)}.p-gray{background:var(--grayb);color:var(--gray)}
.mut{color:var(--mut);font-size:13px}.msg{margin:8px 0;word-break:break-word}
.dots{display:flex;gap:4px;margin-top:10px}.dot{width:14px;height:14px;border-radius:4px;background:var(--grayb)}
.dot.y{background:var(--ok)}
button.btn,.btn{background:var(--ac);color:#fff;border:0;border-radius:8px;padding:8px 16px;font:inherit;cursor:pointer}
button.btn:disabled{opacity:.5;cursor:default}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:14px;background:var(--okb);color:var(--ok)}
.flash.err{background:var(--badb);color:var(--bad)}.flash ul{margin:4px 0 0 18px;padding:0}
label{display:block;margin:12px 0 4px;font-weight:600}
input[type=text],input[type=password],textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--fg);font:inherit}
textarea{min-height:84px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.hint{color:var(--mut);font-size:13px;margin-top:3px}
.chk{display:flex;align-items:center;gap:8px;font-weight:600;margin:0}.chk input{width:18px;height:18px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:600;border-top:0}
@media(max-width:640px){.hide-s{display:none}}
.login{max-width:340px;margin:18vh auto 0}
"""

KIND_PILL = {"ok": ("签到成功", "p-ok"), "already": ("已签到", "p-ok"), "soft": ("等待重试", "p-warn"),
             "auth": ("令牌失效", "p-bad"), "fail": ("失败", "p-bad")}
FLASH = {"saved": ("设置已保存", False), "started": ("已开始签到，几秒后刷新结果", False),
         "busy": ("这个任务正在签到中，请稍候", True), "notcfg": ("还没配置令牌，请先到「设置」填写", True),
         "bad": ("请求无效，请刷新页面重试", True)}


def page(title, body, active="", csrf=None):
    nav = ""
    if csrf is not None:
        nav = ('<nav><a class="%s" href="/">总览</a><a class="%s" href="/settings">设置</a>'
               '<form method="post" action="/logout" style="margin:0"><input type="hidden" name="csrf" value="%s">'
               '<button type="submit">退出</button></form></nav>') % (
            "on" if active == "home" else "", "on" if active == "settings" else "", E(csrf))
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            '<title>%s</title><style>%s</style></head><body><div class="wrap"><header><h1>签到中心</h1>%s</header>%s'
            '</div></body></html>') % (E(title), CSS, nav, body)


def pill_for(t):
    if not t["configured"]:
        return "未配置", "p-gray"
    if not t["enabled"]:
        return "已停用", "p-gray"
    if t["running"]:
        return "签到中…", "p-warn"
    if t["done"]:
        return "今日已签", "p-ok"
    last = t["last"]
    if last and str(last.get("ts", "")).startswith(bj_date()):
        return KIND_PILL.get(last["kind"], ("待签到", "p-gray"))
    return "待签到", "p-gray"


def dots_for(name, hist):
    days = []
    for i in range(13, -1, -1):
        days.append(time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - i * 86400)))
    got = {x["ts"][:10] for x in hist if x.get("task") == name and x.get("kind") in ("ok", "already")}
    return "".join('<span class="dot%s" title="%s"></span>' % (" y" if d in got else "", d) for d in days)


def render_home(csrf, flash_key=""):
    snap = core.snapshot()
    full = load_json(core.HISTORY_FILE, [])
    full = full if isinstance(full, list) else []
    fl = ""
    if flash_key in FLASH:
        m, bad = FLASH[flash_key]
        fl = '<div class="flash%s">%s</div>' % (" err" if bad else "", E(m))
    cards = []
    for t in snap["tasks"]:
        label, cls = pill_for(t)
        last, info = t["last"], t["info"] or {}
        bits = []
        if info.get("balance") is not None:
            bits.append("积分余额 %s" % E(_n(info["balance"])))
        if info.get("streak") is not None:
            bits.append("连签 %s 天" % E(str(info["streak"])))
        if info.get("total") is not None:
            bits.append("本期累计 %s 分" % E(_n(info["total"])))
        if t["enabled"]:
            bits.append("每天 %s 起签" % E(t["time"]))
        if t["next"]:
            bits.append("下次重试 %s" % E(t["next"]))
        lastline = ('<div class="msg">%s<div class="mut">%s · %s</div></div>' % (
            E(last["msg"]), E(last["ts"]), "手动" if last.get("trigger") == "manual" else "自动")) if last else \
            '<div class="msg mut">还没有签到记录</div>'
        btn = ('<form method="post" action="/run/%s"><input type="hidden" name="csrf" value="%s">'
               '<button class="btn" type="submit"%s>立即签到</button></form>') % (
            E(t["key"]), E(csrf), "" if (t["configured"] and not t["running"]) else " disabled")
        cards.append('<div class="card"><div class="row"><h2>%s</h2><span class="pill %s">%s</span></div>%s'
                     '<div class="mut">%s</div><div class="dots" title="近 14 天">%s</div>'
                     '<div class="row" style="margin-top:12px"><span class="mut">近 14 天</span>%s</div></div>' % (
                         E(t["name"]), cls, E(label), lastline, E(" · ".join(bits)) or "&nbsp;",
                         dots_for(t["name"], full), btn))
    rows = []
    for x in snap["history"]:
        lab, cls = KIND_PILL.get(x.get("kind"), (x.get("kind", "?"), "p-gray"))
        rows.append('<tr><td>%s<div class="mut">%s</div></td><td><span class="pill %s">%s</span></td>'
                    '<td>%s</td><td class="hide-s mut">%s</td></tr>' % (
                        E(x.get("task", "")), E(str(x.get("ts", ""))[5:16]), cls, E(lab), E(x.get("msg", "")),
                        "手动" if x.get("trigger") == "manual" else "自动"))
    hist = ('<div class="card"><h2>最近记录</h2><table><tr><th>任务</th><th>结果</th><th>详情</th>'
            '<th class="hide-s">方式</th></tr>%s</table></div>') % ("".join(rows) or
                                                                  '<tr><td colspan="4" class="mut">暂无记录</td></tr>')
    body = fl + '<div class="grid">%s</div>%s<div class="mut">现在是北京时间 %s</div>' % ("".join(cards), hist, E(snap["now"]))
    html_ = page("签到中心", body, "home", csrf)
    if any(t["running"] for t in snap["tasks"]):  # 有任务在跑：3 秒后自动刷新（不用脚本）
        html_ = html_.replace("<title>", '<meta http-equiv="refresh" content="3"><title>', 1)
    return html_


def _n(v):
    try:
        f = float(v)
        return "{:,.0f}".format(f) if f == int(f) else "{:,.2f}".format(f)
    except Exception:  # noqa: BLE001
        return str(v)


def mask(v):
    v = (v or "").strip()
    if not v:
        return "未设置"
    if len(v) <= 12:
        return "已设置（%d 位）" % len(v)
    return "已设置：%s…%s（共 %d 位）" % (v[:4], v[-4:], len(v))


def render_settings(csrf, errors=None, saved=False, form=None):
    form = form or {}

    def val(k, default=""):
        return E(form[k] if k in form else env(k, default))

    def chk(k):
        on = (form.get(k) == "1") if k in form else (env(k, "1") != "0")
        return " checked" if on else ""

    fl = ""
    if saved:
        fl = '<div class="flash">设置已保存。令牌有改动的任务，今天没签成的会立即重试。</div>'
    if errors:
        fl = '<div class="flash err">保存失败：<ul>%s</ul></div>' % "".join("<li>%s</li>" % E(e) for e in errors)
    pem_state = lambda k: "已设置" if env(k) else "未设置"  # noqa: E731

    body = fl + ('<form method="post" action="/settings"><input type="hidden" name="csrf" value="%s">'
                 '<div class="card"><h2>WorkBuddy</h2>'
                 '<label class="chk"><input type="checkbox" name="WORKBUDDY_ENABLED" value="1"%s>启用自动签到</label>'
                 '<label>每天开始签到的时间（北京时间）</label><input type="text" name="WORKBUDDY_TIME" value="%s" placeholder="00:30">'
                 '<label>refreshToken</label><textarea name="WORKBUDDY_REFRESH_TOKEN" placeholder="留空 = 不修改" '
                 'autocomplete="off" spellcheck="false"></textarea>'
                 '<div class="hint">当前：%s。令牌每次续期会自动更新，一般不用再动；失效了才需要重新填。</div></div>'
                 '<div class="card"><h2>Trae（国内版）</h2>'
                 '<label class="chk"><input type="checkbox" name="TRAE_ENABLED" value="1"%s>启用自动签到</label>'
                 '<label>每天开始签到的时间（北京时间）</label><input type="text" name="TRAE_TIME" value="%s" placeholder="00:23">'
                 '<label>refreshToken</label><textarea name="TRAE_REFRESH_TOKEN" placeholder="留空 = 不修改" '
                 'autocomplete="off" spellcheck="false"></textarea><div class="hint">当前：%s</div>'
                 '<label>设备号 TRAE_DEVICE_ID</label><input type="text" name="TRAE_DEVICE_ID" value="%s" inputmode="numeric">'
                 '<label>machineId（建议填）</label><input type="text" name="TRAE_MACHINE_ID" value="%s">'
                 '<label>设备私钥 PEM</label><textarea name="TRAE_DEVICE_KEY_PEM" placeholder="留空 = 不修改" '
                 'autocomplete="off" spellcheck="false"></textarea><div class="hint">当前：%s。支持直接粘贴多行 PEM，或一行里用 \\n 代替换行。</div>'
                 '<label>设备公钥 PEM</label><textarea name="TRAE_DEVICE_PUB_PEM" placeholder="留空 = 不修改" '
                 'autocomplete="off" spellcheck="false"></textarea><div class="hint">当前：%s。这 5 项需从登录着 Trae 的电脑导出，见 README。</div></div>'
                 '<div class="card"><h2>通知（ntfy，可选）</h2><label>主题地址</label>'
                 '<input type="text" name="NTFY_URL" value="%s" placeholder="https://ntfy.example.com/checkin">'
                 '<label>访问令牌（有鉴权才填）</label><input type="password" name="NTFY_TOKEN" placeholder="留空 = 不修改" autocomplete="off">'
                 '<div class="hint">当前：%s</div></div>'
                 '<div class="card"><h2>重试</h2><label>失败后多少分钟重试</label>'
                 '<input type="text" name="RETRY_MIN" value="%s" inputmode="numeric">'
                 '<label>每天最多尝试次数</label><input type="text" name="MAX_TRIES" value="%s" inputmode="numeric"></div>'
                 '<button class="btn" type="submit">保存设置</button></form>') % (
        E(csrf), chk("WORKBUDDY_ENABLED"), val("WORKBUDDY_TIME", "00:30"), E(mask(env("WORKBUDDY_REFRESH_TOKEN"))),
        chk("TRAE_ENABLED"), val("TRAE_TIME", "00:23"), E(mask(env("TRAE_REFRESH_TOKEN"))),
        val("TRAE_DEVICE_ID"), val("TRAE_MACHINE_ID"), pem_state("TRAE_DEVICE_KEY_PEM"), pem_state("TRAE_DEVICE_PUB_PEM"),
        val("NTFY_URL"), "已设置" if env("NTFY_TOKEN") else "未设置", val("RETRY_MIN", "30"), val("MAX_TRIES", "8"))
    return page("设置 - 签到中心", body, "settings", csrf)


TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def clean_secret(v):
    return (v or "").replace("\r", "").strip()


def apply_settings(form):
    """校验并保存。返回 (errors, changed_task_names)。"""
    errors, upd, changed = [], {}, set()

    def f(k):
        return (form.get(k, [""])[0] or "")

    for k, default in (("WORKBUDDY_TIME", "00:30"), ("TRAE_TIME", "00:23")):
        v = f(k).strip() or default
        if not TIME_RE.match(v):
            errors.append("%s 时间格式应为 HH:MM，例如 00:30" % k.split("_")[0].title())
        else:
            h, m = v.split(":")
            upd[k] = "%02d:%s" % (int(h), m)
    for k in ("WORKBUDDY_ENABLED", "TRAE_ENABLED"):
        upd[k] = "1" if f(k) == "1" else "0"
    for k, lo, hi, name in (("RETRY_MIN", 1, 1440, "重试间隔"), ("MAX_TRIES", 1, 50, "最多尝试次数")):
        v = f(k).strip()
        if not v.isdigit() or not lo <= int(v) <= hi:
            errors.append("%s 需为 %d–%d 的整数" % (name, lo, hi))
        else:
            upd[k] = str(int(v))

    # 令牌类：留空 = 不改
    for k, task, label in (("WORKBUDDY_REFRESH_TOKEN", "workbuddy", "WorkBuddy refreshToken"),
                           ("TRAE_REFRESH_TOKEN", "trae", "Trae refreshToken")):
        v = clean_secret(f(k))
        if v:
            if re.search(r"\s", v) or len(v) < 20 or len(v) > 4000:
                errors.append("%s 看起来不对（不能含空白，长度需在 20–4000 之间）" % label)
            elif v != env(k):
                upd[k] = v
                changed.add(task)
    did = f("TRAE_DEVICE_ID").strip()
    if did and not re.fullmatch(r"\d{12,20}", did):
        errors.append("设备号应为 12–20 位数字")
    elif did != env("TRAE_DEVICE_ID"):
        upd["TRAE_DEVICE_ID"] = did
        changed.add("trae")
    mid = f("TRAE_MACHINE_ID").strip()
    if len(mid) > 200 or re.search(r"\s", mid):
        errors.append("machineId 不能含空白")
    elif mid != env("TRAE_MACHINE_ID"):
        upd["TRAE_MACHINE_ID"] = mid
    for k, label in (("TRAE_DEVICE_KEY_PEM", "设备私钥"), ("TRAE_DEVICE_PUB_PEM", "设备公钥")):
        v = clean_secret(f(k))
        if not v:
            continue
        if "\\n" in v and "\n" not in v:
            v = v.replace("\\n", "\n")
        if "BEGIN" not in v or len(v) > 4000:
            errors.append("%s 不是有效的 PEM" % label)
            continue
        if k.endswith("KEY_PEM"):
            try:
                p256.private_scalar(v)
            except Exception:  # noqa: BLE001
                errors.append("设备私钥无法解析（需要 P-256 的 PEM）")
                continue
        if v != env(k):
            upd[k] = v
            changed.add("trae")

    url = f("NTFY_URL").strip()
    if url and not re.match(r"^https?://[^\s]+$", url):
        errors.append("ntfy 地址需以 http:// 或 https:// 开头")
    else:
        upd["NTFY_URL"] = url
    tok = clean_secret(f("NTFY_TOKEN"))
    if tok:
        upd["NTFY_TOKEN"] = tok

    if not errors:
        save_config(upd)
        for t in changed:
            core.retry_now("Trae" if t == "trae" else "WorkBuddy")
    return errors, changed


# ---------------- 请求处理 ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "checkin-hub"
    sys_version = ""

    def log_message(self, *a):  # 不记录访问日志（地址里没有机密，但也没必要刷屏）
        pass

    # ---- 工具 ----
    def ip(self):
        if env("TRUST_PROXY") == "1":
            xf = self.headers.get("X-Forwarded-For", "")
            if xf:
                return xf.split(",")[0].strip()
        return self.client_address[0]

    def session(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and valid_session(v):
                return v
        return None

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # 不能用 no-referrer：Chrome 会因此在表单提交时发 "Origin: null"，同源检查就会误杀登录
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to, extra=None):
        self.send(303, "", extra=[("Location", to)] + (extra or []))

    def cookie_hdr(self, val, max_age):
        secure = "; Secure" if (env("TRUST_PROXY") == "1" and
                                self.headers.get("X-Forwarded-Proto", "") == "https") else ""
        return ("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d%s" % (COOKIE, val, max_age, secure))

    def read_form(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return None
        return parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    def login_page(self, error=""):
        err = '<div class="flash err">%s</div>' % E(error) if error else ""
        body = ('<div class="login card"><h2>登录</h2>%s<form method="post" action="/login">'
                '<label>密码</label><input type="password" name="password" autofocus autocomplete="current-password">'
                '<div style="margin-top:14px"><button class="btn" type="submit" style="width:100%%">登录</button></div>'
                '</form></div>') % err
        return page("登录 - 签到中心", body)

    # ---- 路由 ----
    def do_GET(self):
        u = urlparse(self.path)
        sess = self.session()
        if u.path == "/healthz":
            return self.send(200, "ok", "text/plain; charset=utf-8")
        if not sess:
            return self.send(200, self.login_page())
        csrf = csrf_token(sess)
        if u.path == "/":
            return self.send(200, render_home(csrf, (parse_qs(u.query).get("m") or [""])[0]))
        if u.path == "/settings":
            return self.send(200, render_settings(csrf, saved=(parse_qs(u.query).get("m") or [""])[0] == "saved"))
        self.send(404, page("404", '<div class="card">页面不存在</div>', "", csrf))

    def do_POST(self):
        u = urlparse(self.path)
        form = self.read_form()
        if form is None:
            return self.send(413, "too large", "text/plain; charset=utf-8")
        if not self.same_origin():
            return self.send(403, "forbidden", "text/plain; charset=utf-8")

        if u.path == "/login":
            ip = self.ip()
            if _blocked(ip):
                return self.send(429, self.login_page("尝试次数过多，请 10 分钟后再试"))
            given = (form.get("password") or [""])[0]
            if hmac.compare_digest(hashlib.sha256(given.encode()).digest(),
                                   hashlib.sha256(password().encode()).digest()):
                return self.redirect("/", [self.cookie_hdr(make_session(), SESSION_DAYS * 86400)])
            _fail(ip)
            log("⚠️ 网页登录失败，来自 %s" % ip)
            time.sleep(1)
            return self.send(401, self.login_page("密码不对"))

        sess = self.session()
        if not sess:
            return self.redirect("/")
        if not hmac.compare_digest((form.get("csrf") or [""])[0], csrf_token(sess)):
            return self.redirect("/?m=bad")

        if u.path == "/logout":
            return self.redirect("/", [self.cookie_hdr("", 0)])
        if u.path == "/settings":
            errors, _ = apply_settings(form)
            if errors:
                flat = {k: v[0] for k, v in form.items()}
                return self.send(400, render_settings(csrf_token(sess), errors=errors, form=flat))
            return self.redirect("/settings?m=saved")
        m = re.fullmatch(r"/run/(trae|workbuddy)", u.path)
        if m:
            key = m.group(1)
            if not core.TASKS[key].configured():
                return self.redirect("/?m=notcfg")
            if core.RUN_LOCKS[key].locked():
                return self.redirect("/?m=busy")
            threading.Thread(target=core.execute, args=(key, "manual"), daemon=True).start()
            time.sleep(0.3)  # 让“签到中”状态先出现
            return self.redirect("/?m=started")
        self.send(404, "not found", "text/plain; charset=utf-8")


def serve():
    port = int(env("WEB_PORT", "8080") or 8080)
    pw_from_env = bool(env("WEB_PASSWORD"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    log("网页后台已启动：端口 %d" % port)
    if not pw_from_env:
        log("⚠️ 未设置 WEB_PASSWORD，已生成随机登录密码：%s（保存在 data/.web_password，建议在 .env 里设置 WEB_PASSWORD）" % password())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
