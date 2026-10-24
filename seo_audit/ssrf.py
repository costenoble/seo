"""Garde-fou SSRF : n'autorise que les URLs http(s) dont l'hôte résout vers des adresses publiques.

Indispensable dès que le serveur explore des adresses saisies par des inconnus : sans lui, on pourrait
lui faire sonder le réseau interne ou le service de métadonnées du fournisseur cloud (169.254.169.254)."""
import ipaddress
import socket
from urllib.parse import urlsplit


def check_public_url(url: str) -> None:
    """Lève ValueError si l'URL n'est pas une adresse web publique."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("seuls http et https sont autorisés")
    host = parts.hostname
    if not host:
        raise ValueError("adresse sans hôte")
    if parts.port not in (None, 80, 443, 8080, 8443):
        raise ValueError("port non autorisé")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("hôte introuvable") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("adresse non publique")
        if getattr(ip, "ipv4_mapped", None) and (ip.ipv4_mapped.is_private or ip.ipv4_mapped.is_loopback):
            raise ValueError("adresse non publique")
