"""通过阿里云云助手（Cloud Assistant）在 ECS 上执行 shell 命令。

用途：绕开本机到公网 IP 的异常链路，改走 HTTPS API（ecs.aliyuncs.com:443）。

用法：
  set ALIYUN_AK_ID=... && set ALIYUN_AK_SECRET=...
  python deploy/_aliyun_rc.py --cmd "free -h"
  python deploy/_aliyun_rc.py --file deploy/_steps/13_guest_always_on.sh

环境变量：
  ALIYUN_AK_ID / ALIYUN_AK_SECRET  必填
  ALIYUN_REGION                    默认 cn-shanghai
  ECS_INSTANCE_ID                  默认 i-uf6iflltivqs3pp833eya
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time

from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_tea_openapi import models as open_api_models

DEFAULT_INSTANCE = "i-uf6iflltivqs3pp833eya"


def build_client() -> tuple[EcsClient, str]:
    ak_id = os.environ.get("ALIYUN_AK_ID", "").strip()
    ak_secret = os.environ.get("ALIYUN_AK_SECRET", "").strip()
    region = os.environ.get("ALIYUN_REGION", "cn-shanghai").strip()
    if not ak_id or not ak_secret:
        raise SystemExit("缺少 ALIYUN_AK_ID / ALIYUN_AK_SECRET")
    config = open_api_models.Config(access_key_id=ak_id, access_key_secret=ak_secret)
    config.endpoint = f"ecs.{region}.aliyuncs.com"
    return EcsClient(config), region


def run_command(client: EcsClient, region: str, instance_id: str, script: str) -> int:
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    request = ecs_models.RunCommandRequest(
        region_id=region,
        type="Shell",
        command_content=payload,
        content_encoding="Base64",
        instance_id=[instance_id],
        name=f"dsh-{int(time.time())}",
        timeout=600,
    )
    response = client.run_command(request)
    invoke_id = response.body.invoke_id
    print(f"[RunCommand] invoke_id={invoke_id}", flush=True)

    deadline = time.time() + 600
    last_status = ""
    while time.time() < deadline:
        time.sleep(3)
        query = ecs_models.DescribeInvocationResultsRequest(
            region_id=region, invoke_id=invoke_id
        )
        result = client.describe_invocation_results(query)
        invocation = result.body.invocation
        if not invocation or not invocation.invocation_results:
            continue
        items = invocation.invocation_results.invocation_result or []
        for item in items:
            status = item.invocation_status or ""
            if status != last_status:
                print(f"[status] {status}", flush=True)
                last_status = status
            if status in {"Success", "Failed", "Stopped", "Timeout"}:
                output = item.output or ""
                try:
                    output = base64.b64decode(output).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - 云端偶尔返回明文
                    pass
                print("===== STDOUT =====")
                print(output.rstrip())
                print(f"===== EXIT {item.exit_code} STATUS {status} =====")
                return 0 if status == "Success" else 1
    print("[timeout] 云助手在 600s 内未返回结果")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cmd", help="直接执行的 shell 命令")
    group.add_argument("--file", help="本地 shell 脚本路径，上传后执行")
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            script = handle.read()
        script = f"export LANG=C.UTF-8\n{script}"
    else:
        script = args.cmd or ""

    client, region = build_client()
    instance_id = os.environ.get("ECS_INSTANCE_ID", DEFAULT_INSTANCE).strip()
    return run_command(client, region, instance_id, script)


if __name__ == "__main__":
    sys.exit(main())
