"""公共工具：北京时间、HTTP、状态文件、令牌存储、通知。纯标准库。"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

DATA_DIR = os.getenv("DATA_DIR", "/data")


def env(name, default=""):
    return (os.getenv(name) or default).strip()


# ---------- 时间（固定北京时间，不依赖容器时区） ----------
def _bj():
    return time.gmtime(time.time() + 8 * 3600)


def bj_date():
    return time.strftime("%Y-%m-%d", _bj())


def bj_now():
    return time.strftime("%Y-%m-%d %H:%M:%S", _bj())


def bj_minutes():
    t = _bj()
    return t.tm_hour * 60 + t.tm_min


def log(msg):
    print("[%s] %s" % (bj_now(), msg), flush=True)


# ---------- HTTP ----------
_proxy = env("CHECKIN_PROXY")
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": _proxy, "https": _proxy} if _proxy else {})
)


def post_json(url, headers, body="{}", timeout=30):
    """返回 (http状态码, 文本)；网络异常时返回 (-1, 错误信息)。"""
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


# ---------- 状态文件 ----------
def load_json(name, default):
    try:
        with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, type(default)) else default
    except Exception:  # noqa: BLE001
        return default


def save_json(name, obj):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        log("⚠️ 写入 %s 失败: %s" % (name, e))


class TokenStore:
    """保存轮换后的令牌。

    refreshToken 每次续期都会变，所以必须落盘；.env 里的值只是“种子”。
    如果你改了 .env 里的令牌（种子变了），就丢弃旧缓存、重新用新种子。
    """

    def __init__(self, name, env_refresh_token):
        self.file = "%s_tokens.json" % name
        self.env_rt = env_refresh_token
        self.seed = hashlib.sha256(env_refresh_token.encode()).hexdigest()[:12]

    def load(self):
        d = load_json(self.file, {})
        if d.get("seed") != self.seed or not d.get("refreshToken"):
            d = {"seed": self.seed, "refreshToken": self.env_rt, "accessToken": ""}
        return d

    def save(self, d):
        d["seed"] = self.seed
        save_json(self.file, d)


# ---------- 其它 ----------
def jwt_exp(token):
    """读 JWT 到期时间（秒）；读不到返回 0。兼容 exp / TokenExpireAt（可能是字符串、毫秒）。"""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return 0
    for claim in ("exp", "TokenExpireAt", "tokenExpireAt"):
        raw = payload.get(claim)
        try:
            v = float(str(raw).strip())
        except Exception:  # noqa: BLE001
            continue
        if v <= 0:
            continue
        if v > 1e11:
            v /= 1000.0
        return int(v)
    return 0


def fmt_num(v):
    try:
        f = float(v)
        return "{:,.0f}".format(f) if f == int(f) else "{:,.2f}".format(f)
    except Exception:  # noqa: BLE001
        return str(v)


def result(kind, msg):
    """kind: ok 签到成功 / already 今天已签 / soft 稍后重试 / auth 令牌失效 / fail 其它失败"""
    return {"kind": kind, "msg": msg}


def notify(title, text):
    """推送到 ntfy（配了 NTFY_URL 才推）。令牌绝不写进通知。"""
    log("通知 | %s | %s" % (title, text))
    url = env("NTFY_URL")
    if not url:
        return
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    tok = env("NTFY_TOKEN")
    if tok:
        headers["Authorization"] = "Bearer " + tok
    st, body = post_json(url, headers, "%s\n%s" % (title, text), 15)
    if st < 0 or st >= 400:
        log("⚠️ ntfy 推送失败 http=%s %s" % (st, body[:100]))
