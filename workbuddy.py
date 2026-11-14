"""WorkBuddy「Buddy 加油站」每日签到（每天 100 积分，连签有额外奖励）。

接口来自社区文章（逆向自桌面端 v5.5.x）：refreshToken 换 accessToken，再调签到。
"""
import json

from common import TokenStore, env, fmt_num, log, post_json, result

API = "https://copilot.tencent.com"
EP_REFRESH = API + "/v2/plugin/auth/token/refresh"
EP_CHECKIN = API + "/v2/billing/meter/daily-checkin"
EP_STATUS = API + "/v2/billing/meter/checkin-activity-status"


class WorkBuddy:
    name = "WorkBuddy"

    @staticmethod
    def enabled():
        return bool(env("WORKBUDDY_REFRESH_TOKEN"))

    def __init__(self):
        self.store = TokenStore("workbuddy", env("WORKBUDDY_REFRESH_TOKEN"))
        self.ua = env("WORKBUDDY_UA", "WorkBuddy/5.5.0")

    def _post(self, url, headers):
        h = {"Content-Type": "application/json", "User-Agent": self.ua}
        h.update(headers)
        st, text = post_json(url, h, "{}", 20)
        try:
            d = json.loads(text)
        except Exception:  # noqa: BLE001
            d = None
        return st, d, text

    def run(self):
        tk = self.store.load()

        # 1) 刷新令牌
        st, d, text = self._post(EP_REFRESH, {
            "X-Refresh-Token": tk["refreshToken"],
            "X-Auth-Refresh-Source": "plugin",
            "X-Domain": "copilot.tencent.com",
        })
        if st < 0 or st >= 500:
            return result("soft", "刷新令牌时网络/服务异常 http=%s %s" % (st, text[:80]))
        if not isinstance(d, dict) or d.get("code") != 0 or not d.get("data"):
            code = d.get("code") if isinstance(d, dict) else "?"
            msg = d.get("msg") if isinstance(d, dict) else text[:80]
            return result("auth", "刷新失败 code=%s %s（需重新取令牌填入 .env）" % (code, msg))
        at = d["data"].get("accessToken")
        new_rt = d["data"].get("refreshToken")
        if not at:
            return result("auth", "刷新响应里没有 accessToken")
        if new_rt:
            tk["refreshToken"] = new_rt
            self.store.save(tk)  # 立刻落盘
            log("WorkBuddy: 令牌已续期")

        auth = {"Authorization": "Bearer " + at}

        # 2) 签到（幂等：今天已签返回 10001）
        st, c, text = self._post(EP_CHECKIN, auth)
        if st < 0 or st >= 500:
            return result("soft", "签到请求网络/服务异常 http=%s %s" % (st, text[:80]))
        if not isinstance(c, dict):
            return result("fail", "签到响应无法解析 http=%s %s" % (st, text[:80]))
        code = c.get("code")

        # 3) 连签状态（失败不影响结果）
        extra = ""
        try:
            _, s, _ = self._post(EP_STATUS, auth)
            if isinstance(s, dict) and s.get("code") == 0 and s.get("data"):
                extra = "｜连签 %s 天，本期累计 %s 分" % (
                    s["data"].get("streak_days"), fmt_num(s["data"].get("total_credits")))
        except Exception:  # noqa: BLE001
            pass

        if code == 0:
            got = (c.get("data") or {}).get("credit") if isinstance(c.get("data"), dict) else None
            return result("ok", "签到成功 +%s 积分%s" % (fmt_num(got or 100), extra))
        if code == 10001:
            return result("already", "今日已签到%s" % extra)
        return result("fail", "签到异常 code=%s %s" % (code, c.get("msg")))
