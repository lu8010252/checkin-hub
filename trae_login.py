"""Trae 一键登录（网页生成脚本 + 自动回传令牌）。

Trae 授权页只接受 127.0.0.1 的回调（实测改成盒子地址会直接报「网络错误」），
所以回调必须落在你电脑本机：网页生成一个小脚本，双击运行后它在本机开 127 端口、
打开登录页、把授权码转给盒子，盒子用自己的设备身份换令牌并写进配置。
"""
import base64
import hashlib
import json
import re
import secrets
import time
import urllib.parse
import uuid

import p256
from common import env, log, post_json, save_config

AUTH_URL = "https://www.trae.cn/authorization"
EXCHANGE = "https://api.trae.cn/trae/api/v3/oauth/ExchangeToken"
CALLBACK = "http://127.0.0.1:8931/authorize"
TTL = 1800

_sessions = {}


def _gc():
    now = time.time()
    for k in [k for k, v in _sessions.items() if now - v["ts"] > TTL]:
        _sessions.pop(k, None)


def new_session():
    sid = secrets.token_urlsafe(18)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    _sessions[sid] = {"verifier": verifier, "challenge": challenge, "ts": time.time(),
                      "status": "waiting", "msg": "等待脚本运行…"}
    _gc()
    return sid


SID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
BOX_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


def valid_sid(sid):
    """sid 必须是本机生成过、没过期的会话，并且只含安全字符。"""
    s = _sessions.get(sid or "")
    return bool(SID_RE.match(sid or "") and s and time.time() - s["ts"] <= TTL)


def status(sid):
    s = _sessions.get(sid)
    if not s:
        return "missing", "登录会话不存在或已过期，请重新生成脚本"
    if time.time() - s["ts"] > TTL:
        return "expired", "登录会话已过期（30 分钟），请重新生成脚本"
    return s["status"], s["msg"]


def _device_info():
    return {
        "DeviceID": env("TRAE_DEVICE_ID"),
        "MachineID": env("TRAE_MACHINE_ID"),
        "PlatformCode": "IDE_PC",
        "DeviceType": "PC",
        "DeviceName": env("TRAE_DEVICE_NAME", "PC"),
        "DeviceModel": env("TRAE_DEVICE_MODEL"),
        "ClientVersion": env("TRAE_IDE_VERSION", "1.107.1"),
        "DevicePublicKey": env("TRAE_DEVICE_PUB_PEM"),
        "DeviceBrand": env("TRAE_DEVICE_BRAND"),
        "DeviceCPU": "",
        "OSInfo": env("TRAE_OS", "Windows"),
        "OSVersion": env("TRAE_OS_VERSION", "10.0.19045"),
    }


def ensure_device():
    """设置里没有设备号时，给盒子生成一个自己的设备身份（设备号、machineId、一对 P-256 密钥）。

    已经填过设备号的（比如从电脑导出的）完全不动。想重置身份：把设置里的设备号清空再登录一次。
    返回 True 表示这次新生成了。"""
    if env("TRAE_DEVICE_ID"):
        return False
    priv, pub = p256.generate_keypair()
    save_config({
        "TRAE_DEVICE_ID": "7" + str(secrets.randbelow(10 ** 18)).zfill(18),
        "TRAE_MACHINE_ID": secrets.token_hex(32),
        "TRAE_DEVICE_KEY_PEM": priv,
        "TRAE_DEVICE_PUB_PEM": pub,
    })
    log("Trae: 设置里没有设备身份，已为盒子生成一个新的")
    return True


def login_url(sid):
    """给脚本用的登录地址（PKCE + 本机回调）。"""
    s = _sessions.get(sid)
    if not s:
        return None, "登录会话不存在或已过期"
    ensure_device()
    missing = [k for k in ("TRAE_DEVICE_ID", "TRAE_DEVICE_PUB_PEM") if not env(k)]
    if missing:
        return None, "缺少 %s，先到「设置」里补齐（或清空设备号让盒子自动生成）" % " / ".join(missing)
    version = env("TRAE_IDE_VERSION", "1.107.1")
    q = {
        "login_version": "1",
        "auth_from": "trae",
        "login_channel": "native_ide",
        "plugin_version": "local",
        "auth_type": "local",
        "client_id": env("TRAE_CLIENT_ID", "ono9krqynydwx5"),
        "redirect": "0",
        "login_trace_id": str(uuid.uuid4()),
        "auth_callback_url": CALLBACK,
        "machine_id": env("TRAE_MACHINE_ID"),
        "device_id": env("TRAE_DEVICE_ID"),
        "code_challenge": s["challenge"],
        "code_challenge_method": "S256",
        "x_device_id": env("TRAE_DEVICE_ID"),
        "x_machine_id": env("TRAE_MACHINE_ID"),
        "x_device_type": env("TRAE_OS", "Windows"),
        "x_os_version": env("TRAE_OS_VERSION", "10.0.19045"),
        "x_app_version": version,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(q), None


def complete(sid, query):
    """脚本把回调参数原样转过来，这里换令牌并落盘。"""
    s = _sessions.get(sid)
    if not s or not valid_sid(sid):
        return False, "登录会话不存在或已过期"
    if s["status"] == "ok":
        return True, s["msg"]
    params = urllib.parse.parse_qs(query or "", keep_blank_values=True)
    code = ""
    try:
        code = (json.loads((params.get("authCodeInfo") or [""])[0]) or {}).get("AuthCode") or ""
    except Exception:  # noqa: BLE001
        pass
    if not code:
        code = (params.get("auth_code") or [""])[0]
    if not code:
        s["status"], s["msg"] = "error", "没收到授权码（回调参数：%s）" % (",".join(list(params)[:6]) or "空")
        log("Trae: 登录回调缺授权码")
        return False, s["msg"]
    body = json.dumps({
        "ClientID": env("TRAE_CLIENT_ID", "ono9krqynydwx5"),
        "AuthCode": code,
        "CodeVerifier": s["verifier"],
        "DeviceInfo": _device_info(),
        "IDEVersion": env("TRAE_IDE_VERSION", "1.107.1"),
    })
    st, text = post_json(EXCHANGE, {"Content-Type": "application/json", "x-cloudide-token": ""}, body, 30)
    res = {}
    try:
        res = json.loads(text)["Result"] or {}
    except Exception:  # noqa: BLE001
        pass
    rt = res.get("RefreshToken")
    if not rt:
        s["status"], s["msg"] = "error", "换令牌失败 http=%s %s" % (st, text[:160])
        log("Trae: 登录换令牌失败 http=%s" % st)
        return False, s["msg"]
    save_config({"TRAE_REFRESH_TOKEN": rt})
    s["status"], s["msg"] = "ok", "登录成功，令牌已更新"
    log("Trae: 网页一键登录成功，配置里的令牌已更新")
    try:
        import core
        core.retry_now("Trae")
        s["msg"] += "，已安排重新签到"
    except Exception:  # noqa: BLE001
        pass
    return True, s["msg"]


SCRIPT = r'''# -*- coding: utf-8 -*-
"""Trae 一键登录助手：在你自己电脑上双击运行即可。

它会做三件事：在本机 127.0.0.1:8931 接收 Trae 的登录回调、打开登录页、
把授权码转发给盒子。登录完这个窗口会提示结果，可以关掉。
"""
import http.server
import json
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

BOX = __BOX__
SID = __SID__
PORT = 8931


def call(path, data=None):
    req = urllib.request.Request(
        BOX + path,
        data=(data.encode("utf-8") if data is not None else None),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    try:
        info = call("/trae/url?s=" + SID)
    except Exception as e:
        print("连不上盒子 %s：%s" % (BOX, e))
        return
    if not info.get("url"):
        print("拿不到登录地址：%s" % info.get("error", ""))
        return
    done = {"ok": False, "msg": ""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/authorize":
                self.send_response(404)
                self.end_headers()
                return
            try:
                r = call("/trae/code?s=" + SID, u.query)
                done["ok"], done["msg"] = bool(r.get("ok")), r.get("msg", "")
            except Exception as e:
                done["msg"] = "转发授权码失败：%s" % e
            html = ("<meta charset='utf-8'><body style='font:16px/1.6 -apple-system,"
                    "Microsoft YaHei,sans-serif;padding:40px'><h2>%s</h2><p>%s</p>"
                    "<p style='color:#666'>本页是登录助手起的本地服务，可以关闭。</p></body>") % (
                "登录成功，可以关闭此页面" if done["ok"] else "登录失败", done["msg"])
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print("本地端口 %d 被占用（%s），关掉占用它的程序再试" % (PORT, e))
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("正在打开 Trae 登录页，请在弹出的浏览器里完成登录…")
    webbrowser.open(info["url"])
    print("（如果浏览器没自动打开，手动访问：%s）" % info["url"])
    for _ in range(900):
        if done["msg"]:
            break
        time.sleep(0.5)
    srv.shutdown()
    print("")
    print(("✅ %s" if done["ok"] else "❌ %s") % (done["msg"] or "等待超时，请回网页重新生成脚本"))
    print("令牌已直接写入盒子，不用复制粘贴。")


if __name__ == "__main__":
    main()
    try:
        input("\n按回车键退出…")
    except Exception:
        pass
'''


def script_text(box, sid):
    """生成给用户下载的脚本。box/sid 都经过校验，并用 json.dumps 转成安全的字符串字面量，
    防止有人构造链接往脚本里注入代码。校验不过返回 None。"""
    box = (box or "").rstrip("/")
    if not BOX_RE.match(box) or not valid_sid(sid):
        return None
    return SCRIPT.replace("__BOX__", json.dumps(box)).replace("__SID__", json.dumps(sid))


def render(csrf, sid=""):
    import web

    E = web.E
    head = '<div class="card"><h2>Trae 一键登录</h2>'
    steps = ('<ol class="mut" style="margin:6px 0 0 18px;padding:0">'
             '<li>下载下面的登录脚本（就一个 .py 文件）</li>'
             '<li>在你自己电脑上双击运行（需要电脑装了 Python 3）</li>'
             '<li>浏览器会自动打开 Trae 登录页，登录完令牌自动写回盒子</li></ol>')
    again = ('<form method="post" action="/login/trae"><input type="hidden" name="csrf" value="%s">'
             '<button class="btn" type="submit">重新生成脚本</button></form>') % E(csrf)
    if not sid:
        body = (head + '<div class="msg mut">Trae 的登录回调只能落在打开登录页的那台电脑上（127.0.0.1），'
                       '所以没法用手机扫码；这个脚本把最后一步自动化了，你只需要双击一次。'
                       '<br>设置里没填设备号的话，会自动给盒子生成一个自己的设备身份，不用再从电脑导出，'
                       '也不会影响电脑上的 Trae。</div>%s'
                       '<form method="post" action="/login/trae"><input type="hidden" name="csrf" value="%s">'
                       '<button class="btn" type="submit">生成登录脚本</button></form></div>') % (steps, E(csrf))
        return web.page("Trae 一键登录 - 签到中心", body, "settings", csrf)

    st, msg = status(sid)
    if st == "ok":
        body = (head + '<div class="flash">%s</div><div class="msg mut">回<a href="/">总览</a>看签到结果。</div>%s</div>'
                ) % (E(msg), again)
        return web.page("Trae 一键登录 - 签到中心", body, "settings", csrf)
    if st in ("waiting",):
        body = (head + '<div class="row" style="gap:10px;margin:10px 0"><a class="btn" href="/login/trae/helper.py?s=%s"'
                       '>下载登录脚本</a><span class="pill p-warn">%s</span></div>%s'
                       '<div class="mut" style="margin-top:10px">脚本 30 分钟内有效；本页会自动刷新。</div>%s</div>'
                ) % (E(sid), E(msg), steps, again)
        html_ = web.page("Trae 一键登录 - 签到中心", body, "settings", csrf)
        return html_.replace("<title>", '<meta http-equiv="refresh" content="3"><title>', 1)
    body = (head + '<div class="flash err">%s</div>%s</div>') % (E(msg), again)
    return web.page("Trae 一键登录 - 签到中心", body, "settings", csrf)
