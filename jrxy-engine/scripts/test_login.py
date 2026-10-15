# -*- coding: utf-8 -*-
"""登录连通性测试：仅执行 CAS 登录，不查询任务、不提交签到。

用法：
  python scripts/test_login.py
  可选环境变量：JRXY_USERNAME / JRXY_PASSWORD（Web 页面保存的账号，优先于 config.yml）

输出（机器可读，供 web-server 解析；以 ###LOGIN### 开头的一行 JSON）：
  {"ok":true,"host":"https://hstc.campusphere.net/","cookies":["MOD_AUTH_CAS","CASTGC","CONVERSATION"]}
失败时：
  {"ok":false,"error":"..."}
"""
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from actions.wiseLoginService import wiseLoginService


def main():
    conf = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "config.yml"), encoding="utf-8"))
    user = conf["users"][0]["user"]
    # Web 页面保存的账号密码优先（环境变量传入）；两个变量必须同时存在才生效。
    if os.getenv("JRXY_USERNAME") and os.getenv("JRXY_PASSWORD"):
        user["username"] = os.getenv("JRXY_USERNAME")
        user["password"] = os.getenv("JRXY_PASSWORD")

    def emit(payload):
        print("###LOGIN###" + json.dumps(payload, ensure_ascii=False))

    try:
        wise = wiseLoginService(user, user.get("httpProxy", ""))
        wise.login()
        cookies = sorted(wise.session.cookies.keys())
        emit({
            "ok": True,
            "host": getattr(wise, "campus_host", ""),
            "cookies": cookies,
        })
    except Exception as e:
        emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
