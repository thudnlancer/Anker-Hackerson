import json
import re
import time

import requests

s = requests.Session()
s.trust_env = False
tok = s.post("http://52.41.23.84:45678/api/auth/login",
             json={"username": "contestant", "password": "Solix#2026"}, timeout=15).json()["token"]
h = {"Authorization": "Bearer " + tok}
for i in range(3):
    c = s.get("http://52.41.23.84:45678/api/contest", headers=h, timeout=15).json()
    st = s.get("http://52.41.23.84:45678/api/states", headers=h, timeout=15).text
    times = sorted(set(re.findall(r'"device_time":\s*"([^"]+)"', json.dumps(st))))
    print(c["mode"], "| sim_now:", c.get("now"), "| speed:", c.get("speed"),
          "| seconds_remaining:", c.get("seconds_remaining"), "| device_times:", times[:3])
    time.sleep(20)
