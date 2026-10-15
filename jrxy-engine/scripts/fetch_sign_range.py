# -*- coding: utf-8 -*-
"""从服务端任务详情获取查寝任务签到范围（signPlaceSelected：中心点 + 半径）。

用法：
  python scripts/fetch_sign_range.py
  可选环境变量：JRXY_USERNAME / JRXY_PASSWORD（Web 页面保存的账号，优先于 config.yml）

输出（机器可读，供 web-server 解析；以 ###RANGE### 开头的一行 JSON）：
  {"ok":true,"taskName":"...","signInstanceWid":"...","signWid":"...",
   "lon":116.68000000000000,"lat":23.66000000000000,"radius":500,
   "address":"签到地点","window_begin":"...","window_end":"..."}
失败时：
  {"ok":false,"error":"..."}
"""
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from actions.wiseLoginService import wiseLoginService
from actions.autoSign import AutoSign


def main():
    conf = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "config.yml"), encoding="utf-8"))
    user = conf["users"][0]["user"]
    # Web 页面保存的账号密码优先（环境变量传入）；两个变量必须同时存在才生效。
    if os.getenv("JRXY_USERNAME") and os.getenv("JRXY_PASSWORD"):
        user["username"] = os.getenv("JRXY_USERNAME")
        user["password"] = os.getenv("JRXY_PASSWORD")

    def emit(payload):
        print("###RANGE###" + json.dumps(payload, ensure_ascii=False))

    try:
        wise = wiseLoginService(user, user.get("httpProxy", ""))
        wise.login()
        sign = AutoSign(wise, user)
        # 直接查当天任务列表：未签到/请假/已签到均可（已签后任务会出现在 signedTasks）
        headers = dict(wise.session.headers)
        headers['Content-Type'] = 'application/json'
        res = wise.session.post(wise.campus_host + sign.apis[0],
                                headers=headers, data=json.dumps({}), verify=False).json()
        datas = res.get('datas', {})
        task = None
        for key in ('unSignedTasks', 'leaveTasks', 'signedTasks', 'registerLeaveTasks'):
            lst = datas.get(key) or []
            if lst:
                task = lst[0]
                break
        if task is None:
            emit({"ok": False, "error": "当天没有查到查寝任务（未签到/已签到列表均为空）"})
            return
        sign.taskInfo = {'signInstanceWid': task['signInstanceWid'],
                         'signWid': task['signWid']}
        sign.getDetailTask()
        t = sign.task
        places = t.get("signPlaceSelected") or []
        if not places:
            emit({"ok": False, "error": "任务详情中无 signPlaceSelected（可能不是定位型查寝任务）"})
            return
        p = places[0]
        emit({
            "ok": True,
            "taskName": t.get("taskName", ""),
            "signInstanceWid": t.get("signInstanceWid", ""),
            "signWid": sign.taskInfo.get("signWid", ""),
            "lon": float(p["longitude"]),
            "lat": float(p["latitude"]),
            "radius": int(p.get("radius") or 0),
            "address": p.get("address", ""),
            "window_begin": t.get("singleTaskBeginTime", ""),
            "window_end": t.get("singleTaskEndTime", ""),
        })
    except Exception as e:
        emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
