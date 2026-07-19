from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeTargetError(ValueError):
    pass


def validate_public_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeTargetError("Разрешены только http и https")
    if not parsed.hostname:
        raise UnsafeTargetError("В адресе отсутствует домен")
    if parsed.username or parsed.password:
        raise UnsafeTargetError("Адрес не должен содержать логин или пароль")

    hostname = parsed.hostname.casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise UnsafeTargetError("Локальные адреса запрещены")

    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeTargetError(f"Не удалось определить адрес домена: {exc}") from exc

    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeTargetError("Домен указывает на локальный или служебный IP-адрес")
