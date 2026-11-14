"""Trae 国内版每日签到。

流程：refreshToken(带设备证明) 换 accessToken -> 查状态 -> 领取 -> 回查确认。
refreshToken 是轮换链，每次续期后立刻落盘到 /data。
"""
import json
import os
import time

import p256
from common import (TokenStore, env, fmt_num, jwt_exp, log, post_json, result)

UG = "https://api.trae.cn"
EXCHANGE_PATH = "/trae/api/v3/oauth/ExchangeToken"
EP_STATUS = UG + "/trae/api/v2/ug/checkin_credits/status"
EP_CLAIM = UG + "/trae/api/v2/ug/checkin_credits/claim"
EP_ENTITLE = UG + "/trae/api/v2/pay/user_current_entitlement_list"
OLD_CLIENT = "en1oxy7wnw8j9n"
REQ_BODY = '{"req_source": 1}'

AUTH_FAIL = (1001, 1002, 401, 403)
ALREADY = (9004, 9095)        # 本设备今天已签
RATE = 9074                   # 排队限流 / 设备号不对
RATE_HTTP = (429, 500, 502, 503, 504)


class AuthError(Exception):
    pass


def norm_pem(v):
    """把 .env 里字面量的 \\n 还原成真换行。"""
    v = (v or "").strip().strip('"').strip("'")
    if "\\n" in v and "\n" not in v:
        v = v.replace("\\n", "\n")
    return v


def explain_err(text):
    try:
        err = (json.loads(text).get("ResponseMetadata") or {}).get("Error") or {}
        code = err.get("Code")
        msg = (err.get("Data") or {}).get("__Message.error") or err.get("Message") or ""
    except Exception:  # noqa: BLE001
        return text[:150]
    hint = {
        20101: "refreshToken 已失效/被轮换掉，需要重新导出",
        10101: "refreshToken 与设备不匹配，需要重新导出（设备密钥要和令牌来自同一台电脑）",
        20405: "服务端要求设备证明，检查 TRAE_DEVICE_KEY_PEM / PUB_PEM / DEVICE_ID",
        20403: "设备与令牌不匹配，需在签发该令牌的电脑上重新导出",
    }.get(code, "")
    return "code=%s %s%s" % (code, msg, "（%s）" % hint if hint else "")


class Trae:
    name = "Trae"

    @staticmethod
    def enabled():
        return bool(env("TRAE_REFRESH_TOKEN"))

    def __init__(self):
        self.did = env("TRAE_DEVICE_ID")
        self.machine = env("TRAE_MACHINE_ID")
        self.priv = norm_pem(os.getenv("TRAE_DEVICE_KEY_PEM", ""))
        self.pub = norm_pem(os.getenv("TRAE_DEVICE_PUB_PEM", ""))
        self.client_id = env("TRAE_CLIENT_ID", "ono9krqynydwx5")
        self.version = env("TRAE_IDE_VERSION", "1.107.1")
        self.store = TokenStore("trae", env("TRAE_REFRESH_TOKEN"))

    # ---- 请求头 ----
    def _headers(self, token):
        h = {
            "Authorization": "Cloud-IDE-JWT " + token,
            "X-User-Region": "CN",
            "User-Agent": "Trae/" + self.version,
            "Content-Type": "application/json",
            "x-device-id": self.did,
            "x-device-type": env("TRAE_OS", "Windows"),
            "x-os-version": env("TRAE_OS_VERSION", "10.0.19045"),
            "x-app-version": self.version,
        }
        brand = env("TRAE_DEVICE_BRAND")
        if brand:
            h["x-device-brand"] = brand
        return h

    # ---- 续期 ----
    def _refresh(self, tk):
        if not (self.priv and self.pub and self.did):
            return False, "缺少 TRAE_DEVICE_KEY_PEM / TRAE_DEVICE_PUB_PEM / TRAE_DEVICE_ID，无法续期"
        rt = tk["refreshToken"]
        combos = [(self.client_id, "IDE_PC")]
        for pair in ((OLD_CLIENT, "IDE_PC"), (OLD_CLIENT, "SOLO_PC")):
            if pair not in combos:
                combos.append(pair)
        last = "无响应"
        for cid, pcode in combos:
            ts = int(time.time())
            nonce = os.urandom(16).hex()
            canonical = "\n".join(["POST", EXCHANGE_PATH, cid, rt, str(ts), nonce])
            try:
                sig = p256.sign_b64(self.priv, canonical.encode("utf-8"))
            except Exception as e:  # noqa: BLE001
                return False, "设备私钥解析/签名失败: %s" % e
            info = {
                "DeviceID": self.did,
                "MachineID": self.machine,
                "PlatformCode": pcode,
                "DeviceType": "PC",
                "DeviceName": env("TRAE_DEVICE_NAME", "PC"),
                "DeviceModel": env("TRAE_DEVICE_MODEL"),
                "ClientVersion": self.version,
                "DevicePublicKey": self.pub,
                "DeviceBrand": env("TRAE_DEVICE_BRAND"),
                "DeviceCPU": "",
                "OSInfo": env("TRAE_OS", "Windows"),
                "OSVersion": env("TRAE_OS_VERSION", "10.0.19045"),
            }
            body = json.dumps({
                "ClientID": cid, "ClientSecret": "", "RefreshToken": rt,
                "DeviceInfo": info,
                "DeviceProof": {"Timestamp": ts, "Nonce": nonce, "Signature": sig},
                "IDEVersion": self.version,
            })
            st, text = post_json(UG + EXCHANGE_PATH,
                                 {"Content-Type": "application/json", "x-cloudide-token": ""}, body)
            if st < 0:
                return False, "网络错误: %s" % text[:100]
            if st < 400:
                try:
                    res = json.loads(text)["Result"]
                    at, new_rt = res.get("Token"), res.get("RefreshToken")
                except Exception:  # noqa: BLE001
                    return False, "续期响应无法解析: %s" % text[:120]
                if not at:
                    return False, "续期响应里没有 Token"
                tk["accessToken"] = at
                if new_rt:
                    tk["refreshToken"] = new_rt
                self.store.save(tk)  # 轮换链，立刻落盘
                log("Trae: 令牌已续期")
                return True, "ok"
            last = "http=%s %s" % (st, explain_err(text))
            if "10101" not in text and "20403" not in text:
                break
        return False, last

    # ---- 带自动续期的请求 ----
    def _ug(self, tk, url, body=REQ_BODY, retry=True):
        st, text = post_json(url, self._headers(tk["accessToken"]), body)
        try:
            d = json.loads(text)
        except Exception:  # noqa: BLE001
            d = None
        code = d.get("code", -1) if isinstance(d, dict) else -1
        if (code in AUTH_FAIL or st in (401, 403)) and retry:
            ok, msg = self._refresh(tk)
            if not ok:
                raise AuthError(msg)
            return self._ug(tk, url, body, False)
        if code in AUTH_FAIL or st in (401, 403):
            raise AuthError("续期后仍鉴权失败 code=%s" % code)
        return st, code, d, text

    def _balance(self, tk):
        try:
            st, text = post_json(EP_ENTITLE, self._headers(tk["accessToken"]),
                                 '{"require_usage": true}', 15)
            d = json.loads(text)
            us = (d.get("data") or d).get("usage_summary") or {}
            total = float(us.get("total_amount") or 0)
            if total > 0:
                return total - float(us.get("consumed_amount") or 0)
        except Exception:  # noqa: BLE001
            pass
        return None

    def _done_msg(self, tk, head):
        bal = self._balance(tk)
        return head + ("，积分余额 %s" % fmt_num(bal) if bal is not None else "")

    # ---- 主流程 ----
    def run(self):
        if not self.did:
            return result("fail", "未配置 TRAE_DEVICE_ID")
        tk = self.store.load()
        try:
            exp = jwt_exp(tk.get("accessToken", ""))
            if not tk.get("accessToken") or (exp and exp < time.time() + 600):
                ok, msg = self._refresh(tk)
                if not ok:
                    return result("auth", msg)

            st, code, d, text = self._ug(tk, EP_STATUS)
            if not isinstance(d, dict):
                return result("soft", "状态查询无有效响应 http=%s %s" % (st, text[:80]))
            if d.get("checked_in"):
                return result("already", self._done_msg(tk, "今日已签到"))
            if d.get("enable") is False:
                return result("fail", "该账号未开放签到（enable=false）")

            st, code, d, text = self._ug(tk, EP_CLAIM)
            if code == 0:
                gained = ((d or {}).get("data") or {}).get("points") if isinstance((d or {}).get("data"), dict) else None
                _, _, d2, _ = self._ug(tk, EP_STATUS)
                if isinstance(d2, dict) and d2.get("checked_in"):
                    head = "签到成功" + (" +%s 积分" % fmt_num(gained) if gained else "")
                    return result("ok", self._done_msg(tk, head))
                return result("soft", "领取接口返回成功，但回查未确认，稍后重试")
            if code in ALREADY:
                return result("already", "今日已签到(%s)" % code)
            if code == RATE or st in RATE_HTTP:
                return result("soft", "服务端限流/排队(%s)，稍后重试。若一直 9074，多半是设备号不对" % (code if code != -1 else st))
            msg = (d or {}).get("message") if isinstance(d, dict) else text[:100]
            return result("fail", "签到失败 code=%s %s" % (code, msg))
        except AuthError as e:
            return result("auth", "令牌失效，需重新导出：%s" % e)
