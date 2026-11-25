#!/usr/bin/env python3
"""本次部署用的极简 SSH 运维小工具（临时工具，不是生产代码）。

凭据全部从环境变量读，不落盘、不上 git：
    DEPLOY_HOST  默认 8.153.183.119
    DEPLOY_USER  默认 root
    DEPLOY_PASS  密码（二选一）
    DEPLOY_KEY   私钥路径（二选一）

用法：
    python deploy/_remote.py cmd "free -h; docker --version"
    python deploy/_remote.py run deploy/_steps/01_setup.sh
    python deploy/_remote.py put deploy-bundle.tar.gz /root/deploy-bundle.tar.gz
    python deploy/_remote.py put frontend/dist.tgz /root/dist.tgz
"""

from __future__ import annotations

import os
import posixpath
import sys
import time

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "8.153.183.119")
PORT = int(os.environ.get("DEPLOY_PORT", "22"))
USER = os.environ.get("DEPLOY_USER", "root")
PASSWORD = os.environ.get("DEPLOY_PASS") or None
KEY_FILE = os.environ.get("DEPLOY_KEY") or None

IDLE_TIMEOUT = float(os.environ.get("DEPLOY_IDLE_TIMEOUT", "900"))


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": HOST,
        "port": PORT,
        "username": USER,
        "timeout": 25,
        "banner_timeout": 40,
        "auth_timeout": 40,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if KEY_FILE:
        kwargs["key_filename"] = KEY_FILE
    else:
        kwargs["password"] = PASSWORD
    client.connect(**kwargs)
    return client


def stream(client: paramiko.SSHClient, command: str) -> int:
    """执行命令并实时打印输出，返回退出码。"""
    transport = client.get_transport()
    assert transport is not None
    channel = transport.open_session()
    channel.get_pty(width=200, height=50)
    channel.set_combine_stderr(True)
    channel.exec_command(command)

    last = time.monotonic()
    buffer = b""
    while True:
        if channel.recv_ready():
            chunk = channel.recv(65536)
            if chunk:
                buffer += chunk
                last = time.monotonic()
                text = buffer.decode("utf-8", errors="replace")
                cut = text.rfind("\n")
                if cut >= 0:
                    sys.stdout.write(text[: cut + 1])
                    sys.stdout.flush()
                    buffer = text[cut + 1 :].encode("utf-8")
                continue
        if channel.exit_status_ready() and not channel.recv_ready():
            break
        if time.monotonic() - last > IDLE_TIMEOUT:
            print(f"\n[timeout] 空闲超过 {IDLE_TIMEOUT:.0f}s，中断", file=sys.stderr)
            channel.close()
            return 124
        time.sleep(0.15)

    if buffer:
        sys.stdout.write(buffer.decode("utf-8", errors="replace"))
        sys.stdout.flush()
    code = channel.recv_exit_status()
    channel.close()
    return code


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    action = argv[1]
    client = connect()
    try:
        if action == "cmd":
            return stream(client, " ".join(argv[2:]))
        if action == "run":
            local = argv[2]
            remote = f"/tmp/_deploy_step_{os.getpid()}.sh"
            sftp = client.open_sftp()
            sftp.put(local, remote)
            sftp.close()
            with open(local, "rb") as handle:
                head = handle.read(2)
            runner = "bash" if head == b"#!" else "bash"
            return stream(client, f"cd /opt/job-graph 2>/dev/null || cd /root; {runner} {remote}; rc=$?; rm -f {remote}; echo \"[step-exit] $rc\"; exit $rc")
        if action == "put":
            local, remote = argv[2], argv[3]
            sftp = client.open_sftp()
            remote_dir = posixpath.dirname(remote)
            if remote_dir:
                try:
                    sftp.stat(remote_dir)
                except OSError:
                    stream(client, f"mkdir -p {remote_dir}")
            size = os.path.getsize(local)
            print(f"[put] {local} -> {remote} ({size / 1048576:.2f} MB)")
            sftp.put(local, remote, callback=lambda done, total: None)
            info = sftp.stat(remote)
            print(f"[put] 完成，远端大小 {info.st_size} 字节")
            sftp.close()
            return 0
        print(f"未知动作: {action}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
