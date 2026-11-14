#!/usr/bin/env python3
"""签到中心：网页后台 + 定时签到（常驻）。

  python app.py          常驻：启动网页后台和定时调度（容器默认）
  python app.py once     立刻把所有已配置的任务各跑一次然后退出（试跑/排错用）
"""
import sys
import time

import core
import web
from common import log


def run_once():
    bad = 0
    for key, cls in core.TASKS.items():
        if not cls.enabled():
            continue
        r = core.execute(key, "manual")
        if r is None:
            continue
        print("%s -> [%s] %s" % (cls.name, r["kind"], r["msg"]), flush=True)
        if r["kind"] not in ("ok", "already"):
            bad += 1
    return 1 if bad else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        if not any(cls.enabled() for cls in core.TASKS.values()):
            log("❌ 没有可用的任务：先在网页「设置」里填令牌，或在 .env 里填 TRAE_REFRESH_TOKEN / WORKBUDDY_REFRESH_TOKEN")
            sys.exit(2)
        sys.exit(run_once())
    web.serve()
    try:
        core.daemon_loop()
    except KeyboardInterrupt:
        pass
    time.sleep(0)


if __name__ == "__main__":
    main()
