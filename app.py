#!/usr/bin/env python3
"""签到调度器：常驻运行，每天到点签到，失败自动重试，结果推送 ntfy。

  python app.py          常驻（容器默认）
  python app.py once     立刻把所有已配置的任务各跑一次然后退出（试跑/排错用）
"""
import random
import sys
import time

from common import (bj_date, bj_minutes, env, load_json, log, notify, result,
                    save_json)
from trae import Trae
from workbuddy import WorkBuddy

STATE_FILE = "schedule.json"


def parse_hm(s, default):
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:  # noqa: BLE001
        log("⚠️ 时间格式不对 %r，改用默认 %s" % (s, default))
        return parse_hm(default, "00:30")


def build_tasks():
    tasks = []
    if Trae.enabled():
        tasks.append((Trae(), parse_hm(env("TRAE_TIME", "00:23"), "00:23")))
    if WorkBuddy.enabled():
        tasks.append((WorkBuddy(), parse_hm(env("WORKBUDDY_TIME", "00:30"), "00:30")))
    return tasks


def safe_run(obj):
    try:
        return obj.run()
    except Exception as e:  # noqa: BLE001 —— 常驻进程不能因为一次异常退出
        return result("fail", "异常 %s: %s" % (type(e).__name__, e))


def run_once(tasks):
    bad = 0
    for obj, _ in tasks:
        r = safe_run(obj)
        log("%s -> [%s] %s" % (obj.name, r["kind"], r["msg"]))
        if r["kind"] not in ("ok", "already"):
            bad += 1
    return 1 if bad else 0


def daemon(tasks):
    retry = max(1, int(env("RETRY_MIN", "30") or 30)) * 60
    max_tries = max(1, int(env("MAX_TRIES", "8") or 8))
    state = load_json(STATE_FILE, {})
    for obj, hm in tasks:
        log("已启用 %s，每天 %02d:%02d（北京时间）起签，失败每 %d 分钟重试" % (obj.name, hm // 60, hm % 60, retry // 60))

    while True:
        today = bj_date()
        for obj, hm in tasks:
            st = state.get(obj.name)
            if not st or st.get("date") != today:
                st = {"date": today, "done": False, "next": 0, "tries": 0, "alerted": False}
                state[obj.name] = st
            if st["done"] or bj_minutes() < hm or time.time() < st["next"]:
                continue

            time.sleep(random.randint(0, 15))  # 错开整点
            r = safe_run(obj)
            st["tries"] += 1
            kind = r["kind"]
            log("%s 第%d次 -> [%s] %s" % (obj.name, st["tries"], kind, r["msg"]))

            if kind == "ok":
                st["done"] = True
                notify("✅ %s 签到成功" % obj.name, r["msg"])
            elif kind == "already":
                st["done"] = True
            elif st["tries"] >= max_tries:
                st["done"] = True
                notify("❌ %s 今日放弃" % obj.name, "已重试 %d 次仍未成功：%s" % (st["tries"], r["msg"]))
            else:
                st["next"] = time.time() + (6 * 3600 if kind == "auth" else retry)
                if kind in ("auth", "fail") and not st["alerted"]:
                    st["alerted"] = True
                    notify("⚠️ %s 签到失败" % obj.name, r["msg"])
            save_json(STATE_FILE, state)
        time.sleep(30)


def main():
    tasks = build_tasks()
    if not tasks:
        log("❌ 没有配置任何任务：请在 .env 里填 TRAE_REFRESH_TOKEN 或 WORKBUDDY_REFRESH_TOKEN")
        sys.exit(2)
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        sys.exit(run_once(tasks))
    daemon(tasks)


if __name__ == "__main__":
    main()
