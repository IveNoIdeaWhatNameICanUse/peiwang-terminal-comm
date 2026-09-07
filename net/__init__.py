# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP网卡枚举
"""本机网卡 IPv4 枚举。"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import List


@dataclass
class NicAddress:
    name: str
    ip: str
    is_loopback: bool = False


def list_ipv4_nics() -> List[NicAddress]:
    """枚举本机可用 IPv4 地址（含回环）。"""
    result: List[NicAddress] = []
    seen = set()

    # 回环
    result.append(NicAddress(name="Loopback", ip="127.0.0.1", is_loopback=True))
    seen.add("127.0.0.1")

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip in seen:
                continue
            seen.add(ip)
            result.append(NicAddress(name=hostname, ip=ip, is_loopback=False))
    except OSError:
        pass

    # Windows：补充通过 UDP 探测到的默认出网地址
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip not in seen:
                seen.add(ip)
                result.append(NicAddress(name="Default", ip=ip, is_loopback=False))
        finally:
            s.close()
    except OSError:
        pass

    # 尝试 psutil 风格之外的 netifaces 不可用时，用 getaddrinfo 0.0.0.0 无意义
    # 补充：遍历常见接口名不可靠，保持上述结果即可
    return result


def nics_as_dicts() -> List[dict]:
    return [
        {"name": n.name, "ip": n.ip, "is_loopback": n.is_loopback}
        for n in list_ipv4_nics()
    ]


# [AGENT_CHANGE_END] 2026-09-07 104-MVP网卡枚举
