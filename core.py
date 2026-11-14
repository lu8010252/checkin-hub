"""任务执行、当日状态、历史记录、调度循环。网页后台和命令行都调用这里。"""
import random
import threading
import time

from common import (bj_date, bj_minutes, bj_now, env, load_json, log, notify,
                    result, save_json)
from trae import Trae
from workbuddy import WorkBuddy

TASKS = {"trae": Trae, "workbuddy": WorkBuddy}
DEFAULT_TIME = {"trae": "00:23", "workbuddy": "00:30"}
STATE_FILE = "schedule.json"
HISTORY_FILE = "history.json"
HISTORY_MAX = 300

STATE_LOCK = threading.RLock()
RUN_LOCKS = {k: threading.Lock() for k in TASKS}
_STATE = load_json(STATE_FILE, {})


def _int_env(name, default, lo, hi):
    try:
        return min(hi, max(lo, int(env(name, str(default)))))
    except ValueError:
        return default


def parse_hm(s, default):
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except Exception:  # noqa: BLE001
        pass
    return parse_hm(default, "00:30") if default != s else 30


def conf(key):
    cls = TASKS[key]
    return {"key": key, "name": cls.name, "configured": cls.configured(),
            "enabled": cls.enabled(), "time": env(cls.TIME_ENV, DEFAULT_TIME[key])}


def day_state(name):
    st = _STATE.get(name)
    if not st or st.get("date") != bj_date():
        st = {"date": bj_date(), "done": False, "next": 0, "tries": 0, "alerted": False}
        _STATE[name] = st
    return st


def retry_now(name):
    """令牌被改过时：今天还没签成的话，立刻允许重试。"""
    with STATE_LOCK:
        st = day_state(name)
        if not st["done"]:
            st.update(next=0, tries=0, alerted=False)
            save_json(STATE_FILE, _STATE)


def record(name, trigger, r):
    h = load_json(HISTORY_FILE, [])
    if not isinstance(h, list):
        h = []
    h.append({"ts": bj_now(), "task": name, "kind": r["kind"], "msg": r["msg"],
              "trigger": trigger, "extra": r.get("extra") or {}})
    save_json(HISTORY_FILE, h[-HISTORY_MAX:])


def safe_run(cls):
    try:
        return cls().run()
    except Exception as e:  # noqa: BLE001 —— 常驻进程不能因为一次异常退出
        return result("fail", "异常 %s: %s" % (type(e).__name__, e))


def execute(key, trigger="auto"):
    """跑一次任务并更新状态/历史/通知。该任务正在跑时返回 None。"""
    cls = TASKS[key]
    lock = RUN_LOCKS[key]
    if not lock.acquire(blocking=False):
        return None
    try:
        r = safe_run(cls)
    finally:
        lock.release()
    kind, name = r["kind"], cls.name
    log("%s [%s] -> [%s] %s" % (name, trigger, kind, r["msg"]))
    notice = None
    with STATE_LOCK:
        record(name, trigger, r)
        st = day_state(name)
        if trigger == "auto":
            st["tries"] += 1
        if kind == "ok":
            st["done"] = True
            notice = ("✅ %s 签到成功" % name, r["msg"])
        elif kind == "already":
            st["done"] = True
        elif trigger == "auto":
            if st["tries"] >= _int_env("MAX_TRIES", 8, 1, 50):
                st["done"] = True
                notice = ("❌ %s 今日放弃" % name, "已重试 %d 次仍未成功：%s" % (st["tries"], r["msg"]))
            else:
                wait = 6 * 3600 if kind == "auth" else _int_env("RETRY_MIN", 30, 1, 1440) * 60
                st["next"] = time.time() + wait
                if kind in ("auth", "fail") and not st["alerted"]:
                    st["alerted"] = True
                    notice = ("⚠️ %s 签到失败" % name, r["msg"])
        save_json(STATE_FILE, _STATE)
    if notice:
        notify(*notice)
    return r


def daemon_loop():
    log("调度已启动：每天到点自动签到，失败自动重试")
    while True:
        try:
            now_min = bj_minutes()
            for key in TASKS:
                c = conf(key)
                if not c["enabled"]:
                    continue
                with STATE_LOCK:
                    st = day_state(c["name"])
                    due = (not st["done"] and now_min >= parse_hm(c["time"], DEFAULT_TIME[key])
                           and time.time() >= st["next"])
                if due:
                    time.sleep(random.randint(0, 10))  # 错开整点
                    execute(key, "auto")
        except Exception as e:  # noqa: BLE001
            log("⚠️ 调度循环异常: %s: %s" % (type(e).__name__, e))
        time.sleep(30)


def snapshot():
    """给网页用的当前状态。"""
    hist = load_json(HISTORY_FILE, [])
    if not isinstance(hist, list):
        hist = []
    tasks = []
    with STATE_LOCK:
        for key, cls in TASKS.items():
            c = conf(key)
            st = day_state(cls.name)
            mine = [x for x in hist if x.get("task") == cls.name]
            nxt = ""
            if st["next"] > time.time() and not st["done"]:
                nxt = time.strftime("%H:%M", time.gmtime(st["next"] + 8 * 3600))
            tasks.append({
                **c, "done": st["done"], "tries": st["tries"], "next": nxt,
                "running": RUN_LOCKS[key].locked(),
                "last": mine[-1] if mine else None,
                "info": next((x["extra"] for x in reversed(mine) if x.get("extra")), {}),
            })
    return {"now": bj_now(), "tasks": tasks, "history": list(reversed(hist[-60:]))}
