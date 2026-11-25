import os
import sys
import time

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "8.153.183.119")
USER = os.environ.get("DEPLOY_USER", "root")
PWD = os.environ.get("DEPLOY_PASS", "")

CMD = sys.argv[1] if len(sys.argv) > 1 else "uptime; free -h"

for attempt in range(1, 9):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            HOST,
            username=USER,
            password=PWD,
            timeout=20,
            banner_timeout=30,
            auth_timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command(CMD, timeout=120)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        print(f"[connected on attempt {attempt}]")
        print(out)
        if err.strip():
            print("[stderr]", err)
        client.close()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"attempt {attempt} failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(5)

print("ALL ATTEMPTS FAILED")
sys.exit(1)
