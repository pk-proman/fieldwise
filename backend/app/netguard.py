"""Outbound-URL safety (SSRF): URLs that admins configure (ERPNext, SMS / WhatsApp / telephony gateways) must not point at
this server's own network, cloud metadata addresses or loopback. Set ALLOW_PRIVATE_URLS=1 only for a self-hosted ERPNext on a private LAN/VPN."""
import ipaddress
import os
import socket
from urllib.parse import urlparse


def _allowed_private() -> bool:
    return os.getenv("ALLOW_PRIVATE_URLS", "0") == "1"


def check_url(url: str, allow_http: bool = False) -> str:
    """Returns the URL if it is safe to call, else raises ValueError with a plain-language reason."""
    if os.getenv("NETGUARD_DISABLE", "0") == "1":
        return url
    u = urlparse((url or "").strip())
    if u.scheme not in (("https", "http") if allow_http or _allowed_private() else ("https",)):
        raise ValueError("The address must start with https://")
    if not u.hostname:
        raise ValueError("The address has no host name.")
    if u.username or u.password:
        raise ValueError("Don't put a username or password in the address.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"'{u.hostname}' could not be found. Check the address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or str(ip) == "169.254.169.254" or (ip.is_private and not _allowed_private()):
            raise ValueError("That address points to a private or internal network, which isn't allowed.")
    return url
