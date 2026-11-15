"""WorkBuddy 扫码登录。

登录链接里的 state 是一次性的、30 分钟内有效，所以不能存成固定二维码；
这里每次点击「生成二维码」都向官方要一个新的 state，页面轮询它，
扫码登录成功后自动把新的 refreshToken 写进配置。
"""
import json
import threading
import time

from common import bj_now, get_json_text, log, post_json, save_config

API = "https://copilot.tencent.com"
HDR = {
    "Content-Type": "application/json",
    "X-Domain": "copilot.tencent.com",
    "X-No-Authorization": "true",
    "X-No-User-Id": "true",
    "X-No-Enterprise-Id": "true",
    "X-No-Department-Info": "true",
}
TTL = 1800  # 一个 state 的有效期（秒）

_sessions = {}
_lock = threading.Lock()


def _get(url, timeout=15):
    st, text = get_json_text(url, HDR, timeout)  # 走 CHECKIN_PROXY，和其它请求一致
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"code": -1, "msg": "http %s %s" % (st, text[:80])}


def new_state():
    """要一个新的登录会话，返回 (state, 错误说明)。"""
    st, text = post_json(API + "/v2/plugin/auth/state?platform=workbuddy", HDR, "{}", 20)
    try:
        d = json.loads(text)
    except Exception:  # noqa: BLE001
        return None, "服务端响应异常 http=%s" % st
    data = d.get("data") or {}
    if d.get("code") != 0 or not data.get("authUrl") or not data.get("state"):
        return None, "生成登录链接失败 code=%s %s" % (d.get("code"), d.get("msg"))
    now = time.time()
    with _lock:
        for k in [k for k, v in _sessions.items() if now - v["ts"] > TTL]:
            _sessions.pop(k, None)
        _sessions[data["state"]] = {"url": data["authUrl"], "ts": now, "done": False, "msg": ""}
    log("WorkBuddy: 已生成扫码登录链接")
    return data["state"], None


def session_url(state):
    with _lock:
        s = _sessions.get(state)
        return s["url"] if s else ""


def poll(state):
    """查一次。返回 (status, msg)：pending / ok / expired / error。"""
    with _lock:
        s = _sessions.get(state)
    if not s:
        return "expired", "登录会话不存在或已过期，请重新生成"
    if s["done"]:
        return "ok", s["msg"]
    if time.time() - s["ts"] > TTL:
        return "expired", "登录会话已过期，请重新生成"
    d = _get(API + "/v2/plugin/auth/token?state=" + state)
    code = d.get("code")
    if code == 11217:
        return "pending", "等待手机扫码登录…"
    if code != 0 or not isinstance(d.get("data"), dict):
        return "error", "登录失败 code=%s %s" % (code, d.get("msg"))
    rt = d["data"].get("refreshToken")
    if not rt:
        return "error", "服务端没返回 refreshToken"
    save_config({"WORKBUDDY_REFRESH_TOKEN": rt})
    with _lock:
        s["done"] = True
        s["msg"] = "登录成功，已换上新令牌（%d 位）" % len(rt)
        msg = s["msg"]
    log("WorkBuddy: 扫码登录成功，配置里的令牌已更新")
    try:
        import core
        core.retry_now("WorkBuddy")
        msg += "，已安排重新签到"
        with _lock:
            s["msg"] = msg
    except Exception:  # noqa: BLE001
        pass
    return "ok", msg


def qr_svg(text, box=6):
    """把文本画成内联 SVG 二维码（页面 CSP 只允许内联样式，所以不用 <img>）。"""
    try:
        import qrcode

        q = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(text)
        q.make(fit=True)
        m = q.get_matrix()
    except Exception as e:  # noqa: BLE001
        log("⚠️ 二维码生成失败: %s" % e)
        return ""
    n = len(m)
    path = "".join("M%d %dh1v1h-1z" % (x, y) for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
            'shape-rendering="crispEdges" role="img" aria-label="登录二维码">'
            '<rect width="%d" height="%d" fill="#fff"/><path d="%s" fill="#000"/></svg>') % (
        n, n, n * box, n * box, n, n, path)


def render_error(csrf, msg):
    import web

    body = ('<div class="card"><h2>WorkBuddy 扫码登录</h2><div class="flash err">%s</div>'
            '<form method="post" action="/login/workbuddy"><input type="hidden" name="csrf" value="%s">'
            '<button class="btn" type="submit">重试</button></form></div>') % (web.E(msg), web.E(csrf))
    return web.page("扫码登录 - 签到中心", body, "settings", csrf)


def render(csrf, state=""):
    """扫码登录页。state 为空 => 显示「生成二维码」按钮。"""
    import web

    E = web.E
    head = '<div class="card"><h2>WorkBuddy 扫码登录</h2>'
    if not state:
        body = (head + '<div class="msg mut">登录链接是一次性的（30 分钟内有效），每次点按钮现生成一个。'
                       '用手机扫出来的二维码登录即可，成功后令牌会自动写进配置。</div>'
                       '<form method="post" action="/login/workbuddy"><input type="hidden" name="csrf" value="%s">'
                       '<button class="btn" type="submit">生成二维码</button></form></div>') % E(csrf)
        return web.page("扫码登录 - 签到中心", body, "settings", csrf)

    status, msg = poll(state)
    url = session_url(state)
    again = ('<form method="post" action="/login/workbuddy"><input type="hidden" name="csrf" value="%s">'
             '<button class="btn" type="submit">重新生成</button></form>') % E(csrf)
    if status == "ok":
        body = (head + '<div class="flash">%s</div><div class="msg mut">可以关掉这个页面了，'
                       '回<a href="/">总览</a>看签到结果。</div>%s</div>') % (E(msg), again)
        return web.page("扫码登录 - 签到中心", body, "settings", csrf)
    if status == "pending":
        svg = qr_svg(url)
        body = (head + '<div class="msg mut">用手机相机 / 微信扫下面的码，登录你的 WorkBuddy 账号；'
                       '本页会自动刷新，成功后令牌自动写入。</div>'
                       '<div style="display:flex;justify-content:center;margin:14px 0">%s</div>'
                       '<div class="mut" style="word-break:break-all">打不开二维码就直接在手机浏览器里打开：'
                       '<a href="%s">%s</a></div>'
                       '<div class="row" style="margin-top:12px"><span class="pill p-warn">%s</span>%s</div></div>') % (
            svg, E(url), E(url), E(msg), again)
        html_ = web.page("扫码登录 - 签到中心", body, "settings", csrf)
        return html_.replace("<title>", '<meta http-equiv="refresh" content="2"><title>', 1)
    body = (head + '<div class="flash err">%s</div><div class="mut">%s</div>%s</div>') % (E(msg), E(bj_now()), again)
    return web.page("扫码登录 - 签到中心", body, "settings", csrf)
