"""容器入口：在原版网页后台的基础上加「扫码登录 / 一键登录」路由。"""
import hmac
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/app")

import core  # noqa: E402
import trae_login  # noqa: E402
import web  # noqa: E402
import wbqr  # noqa: E402
from common import env, log  # noqa: E402

_orig_page = web.page


def _page(title, body, active="", csrf=None):
    """给导航栏补「扫码登录 / Trae 登录」入口。"""
    h = _orig_page(title, body, active, csrf)
    if csrf is not None:
        h = h.replace("<nav>", '<nav><a href="/login/trae">Trae 登录</a>'
                               '<a href="/login/workbuddy">扫码登录</a>', 1)
    return h.replace("失效了才需要重新填。", "失效了直接点上方导航的「扫码登录」扫一下即可。")


web.page = _page


def _q(u):
    return parse_qs(u.query)


class Handler(web.Handler):
    def do_GET(self):
        u = urlparse(self.path)
        q = _q(u)

        if u.path == "/trae/url":  # 脚本取登录地址
            sid = (q.get("s") or [""])[0]
            url, err = trae_login.login_url(sid)
            body = json.dumps({"url": url} if url else {"error": err}, ensure_ascii=False)
            return self.send(200, body, "application/json; charset=utf-8")

        if u.path == "/login/trae/helper.py":  # 下载一键登录脚本
            sid = (q.get("s") or [""])[0]
            if not self.session():  # 和其它页面一样，下载脚本也要先登录后台
                return self.send(200, self.login_page())
            box = "http://" + (self.headers.get("Host") or "127.0.0.1:8090")
            text = trae_login.script_text(box, sid)
            if text is None:
                return self.send(404, "会话不存在、已过期或地址无效，请回网页重新生成", "text/plain; charset=utf-8")
            return self.send(200, text, "application/octet-stream",
                             [("Content-Disposition", 'attachment; filename="trae_login.py"')])

        if u.path == "/login/trae":
            sess = self.session()
            if not sess:
                return self.send(200, self.login_page())
            return self.send(200, trae_login.render(web.csrf_token(sess), (q.get("s") or [""])[0]))

        if u.path == "/login/workbuddy":
            sess = self.session()
            if not sess:
                return self.send(200, self.login_page())
            state = (q.get("state") or [""])[0]
            return self.send(200, wbqr.render(web.csrf_token(sess), state))

        return web.Handler.do_GET(self)

    def do_POST(self):
        u = urlparse(self.path)
        q = _q(u)

        if u.path == "/trae/code":  # 脚本把回调参数转过来
            sid = (q.get("s") or [""])[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > web.MAX_BODY:
                return self.send(413, "too large", "text/plain; charset=utf-8")
            raw = self.rfile.read(n).decode("utf-8", "replace") if n > 0 else ""
            ok, msg = trae_login.complete(sid, raw)
            return self.send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False),
                             "application/json; charset=utf-8")

        if u.path not in ("/login/trae", "/login/workbuddy"):
            return web.Handler.do_POST(self)

        form = self.read_form()
        if form is None:
            return self.send(413, "too large", "text/plain; charset=utf-8")
        if not self.same_origin():
            return self.send(403, "forbidden", "text/plain; charset=utf-8")
        sess = self.session()
        if not sess:
            return self.redirect("/")
        if not hmac.compare_digest((form.get("csrf") or [""])[0], web.csrf_token(sess)):
            return self.redirect("/?m=bad")

        if u.path == "/login/trae":
            return self.redirect("/login/trae?s=" + trae_login.new_session())

        state, err = wbqr.new_state()
        if err:
            return self.send(200, wbqr.render_error(web.csrf_token(sess), err))
        return self.redirect("/login/workbuddy?state=" + state)


def main():
    port = int(env("WEB_PORT", "8080") or 8080)
    pw_from_env = bool(env("WEB_PASSWORD"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    log("网页后台已启动：端口 %d" % port)
    if not pw_from_env:
        log("⚠️ 未设置 WEB_PASSWORD，已生成随机登录密码：%s（保存在 data/.web_password，建议在 .env 里设置 WEB_PASSWORD）"
            % web.password())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    core.daemon_loop()


if __name__ == "__main__":
    main()
