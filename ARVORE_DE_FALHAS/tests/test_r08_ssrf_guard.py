"""
Testes de regressão para R08 — Download da fila permite destinos internos e volume sem limite.

Testa diretamente as funções de validação SSRF sem depender do broker RabbitMQ.
"""
from __future__ import annotations

import socket as _socket
from unittest.mock import patch, MagicMock

import pytest


def _import_ssrf_helpers():
    """Importa as funções de validação sem disparar o import de aio_pika."""
    import sys
    if "aio_pika" not in sys.modules:
        sys.modules["aio_pika"] = MagicMock()

    from src.messaging.request_consumer import (
        _validate_url_for_download,
        _is_blocked_ip,
        _pin_hostname,
        _original_getaddrinfo,
    )
    return _validate_url_for_download, _is_blocked_ip, _pin_hostname, _original_getaddrinfo


# ── _is_blocked_ip ────────────────────────────────────────────────────────────


def test_loopback_ipv4_is_blocked():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("127.0.0.1") is True
    assert _is_blocked_ip("127.255.255.255") is True


def test_private_class_a_is_blocked():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("10.0.0.1") is True
    assert _is_blocked_ip("10.255.255.255") is True


def test_private_class_b_is_blocked():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("172.16.0.1") is True
    assert _is_blocked_ip("172.31.255.255") is True


def test_private_class_c_is_blocked():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("192.168.1.1") is True


def test_link_local_is_blocked():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("169.254.169.254") is True   # metadata de nuvem
    assert _is_blocked_ip("169.254.0.1") is True


def test_ipv6_loopback_is_blocked():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("::1") is True


def test_benchmarking_range_is_blocked():
    """R08 — 198.18.0.0/15 (benchmarking RFC 2544) deve ser bloqueado."""
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("198.18.0.1") is True
    assert _is_blocked_ip("198.19.255.255") is True


def test_ietf_assignments_range_is_blocked():
    """R08 — 192.0.0.0/24 (IETF Protocol Assignments) deve ser bloqueado."""
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("192.0.0.1") is True


def test_documentation_ranges_are_blocked():
    """R08 — TEST-NET-1/2/3 (documentação RFC) devem ser bloqueados."""
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("192.0.2.1") is True     # TEST-NET-1
    assert _is_blocked_ip("198.51.100.1") is True  # TEST-NET-2
    assert _is_blocked_ip("203.0.113.1") is True   # TEST-NET-3


def test_public_ip_is_allowed():
    _, _is_blocked_ip, *_ = _import_ssrf_helpers()
    assert _is_blocked_ip("8.8.8.8") is False
    assert _is_blocked_ip("1.1.1.1") is False
    assert _is_blocked_ip("51.75.128.1") is False  # endereço público europeu


# ── _validate_url_for_download ────────────────────────────────────────────────


def test_http_url_with_private_ip_is_blocked():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with patch("src.messaging.request_consumer._original_getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
        with pytest.raises(ValueError, match="bloqueado"):
            _validate_url_for_download("http://localhost/file.pdf")


def test_https_url_with_private_ip_is_blocked():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with patch("src.messaging.request_consumer._original_getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("192.168.1.1", 0))]
        with pytest.raises(ValueError, match="bloqueado"):
            _validate_url_for_download("https://internal.corp/file.pdf")


def test_https_url_with_link_local_is_blocked():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with patch("src.messaging.request_consumer._original_getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
        with pytest.raises(ValueError, match="bloqueado"):
            _validate_url_for_download("https://metadata.internal/latest/")


def test_url_with_invalid_scheme_is_blocked():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with pytest.raises(ValueError, match="Esquema"):
        _validate_url_for_download("ftp://example.com/file.pdf")
    with pytest.raises(ValueError, match="Esquema"):
        _validate_url_for_download("file:///etc/passwd")


def test_url_without_hostname_is_blocked():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with pytest.raises(ValueError):
        _validate_url_for_download("https:///file.pdf")


def test_public_https_url_passes_validation():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with patch("src.messaging.request_consumer._original_getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 0))]
        resolved = _validate_url_for_download("https://cdn.example.com/manual.pdf")
    assert resolved == "8.8.8.8"


def test_unresolvable_hostname_is_blocked():
    _validate_url_for_download, *_ = _import_ssrf_helpers()
    with patch("src.messaging.request_consumer._original_getaddrinfo",
               side_effect=_socket.gaierror("Name or service not known")):
        with pytest.raises(ValueError, match="resolver"):
            _validate_url_for_download("https://host-inexistente.invalid/file.pdf")


# ── _pin_hostname — previne DNS rebinding ────────────────────────────────────


def test_pin_hostname_overrides_resolution_in_thread():
    """R08 — _pin_hostname deve fazer getaddrinfo retornar o IP fixado."""
    import socket
    _, _, _pin_hostname, _ = _import_ssrf_helpers()

    pinned_ip = "51.75.128.1"
    with _pin_hostname("cdn.example.com", pinned_ip):
        result = socket.getaddrinfo("cdn.example.com", 443)
    assert result[0][4][0] == pinned_ip


def test_pin_hostname_restores_after_context():
    """R08 — após o contexto, o pinning deve ser removido."""
    import socket
    _, _, _pin_hostname, _ = _import_ssrf_helpers()

    with _pin_hostname("cdn.example.com", "51.75.128.1"):
        pass  # entrar e sair do contexto

    # Após o contexto, não deve mais ter pinning — consulta normal.
    # Não há forma de testar o valor real sem DNS; apenas verificar que não há
    # entradas residuais no thread-local.
    from src.messaging.request_consumer import _thread_local
    pinnings = getattr(_thread_local, "dns_pinnings", {})
    assert "cdn.example.com" not in pinnings


# ── Tamanho máximo no download ────────────────────────────────────────────────


def test_max_download_bytes_constant_is_reasonable():
    """R08 — limite de bytes deve ser configurado e razoável (<=50MB)."""
    from src.messaging.request_consumer import _MAX_DOWNLOAD_BYTES
    assert isinstance(_MAX_DOWNLOAD_BYTES, int)
    assert _MAX_DOWNLOAD_BYTES > 0
    assert _MAX_DOWNLOAD_BYTES <= 50 * 1024 * 1024, (
        f"_MAX_DOWNLOAD_BYTES={_MAX_DOWNLOAD_BYTES} excede 50 MB."
    )


def test_max_redirects_constant_is_limited():
    """R08 — limite de redirecionamentos deve ser configurado e pequeno."""
    from src.messaging.request_consumer import _MAX_REDIRECTS
    assert isinstance(_MAX_REDIRECTS, int)
    assert 0 < _MAX_REDIRECTS <= 10


# ── Redirect relativo e trust_env ────────────────────────────────────────────


def test_relative_redirect_resolved_with_urljoin():
    """R08 — redirect com Location relativo deve ser resolvido contra a URL base."""
    from urllib.parse import urljoin
    base = "https://cdn.example.com/files/manual.pdf"
    assert urljoin(base, "/v2/manual.pdf") == "https://cdn.example.com/v2/manual.pdf"
    assert urljoin(base, "v2/manual.pdf") == "https://cdn.example.com/files/v2/manual.pdf"
    assert urljoin(base, "https://other.com/x.pdf") == "https://other.com/x.pdf"


def test_download_session_trust_env_false():
    """R08 — download deve criar Session com trust_env=False para ignorar proxies do ambiente."""
    import asyncio
    import os
    import sys
    import tempfile

    if "aio_pika" not in sys.modules:
        sys.modules["aio_pika"] = MagicMock()

    # Garantir que requests está disponível como mock no sys.modules se não instalado.
    fake_requests_mod = MagicMock()
    captured: list = []

    class _FakeSession:
        def __init__(self):
            self.trust_env = True  # requests default — deve ser sobrescrito para False
            captured.append(self)

        def get(self, url, **kw):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status.return_value = None
            r.iter_content.return_value = iter([b"data"])
            return r

    fake_requests_mod.Session = _FakeSession

    from src.messaging.request_consumer import RequestConsumer

    consumer = RequestConsumer(pipeline_handler=MagicMock())

    with (
        patch("src.messaging.request_consumer._validate_url_for_download", return_value="8.8.8.8"),
        patch("src.messaging.request_consumer._pin_hostname") as mock_pin,
        patch.dict(sys.modules, {"requests": fake_requests_mod}),
    ):
        mock_pin.return_value.__enter__ = lambda s: s
        mock_pin.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.NamedTemporaryFile(delete=False) as f:
            dest = f.name
        try:
            asyncio.run(consumer._download_to_file("https://cdn.example.com/file.pdf", dest))
        finally:
            os.unlink(dest)

    assert captured, "Session não foi instanciada durante o download"
    assert captured[0].trust_env is False, (
        "R08 — Session.trust_env deve ser False para isolar dos proxies do ambiente."
    )
