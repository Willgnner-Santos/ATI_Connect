"""Wrappers finos sobre requests para falar com a API local (sam local start-api).

Toda resposta da API segue o padrão Lambda-proxy: corpo JSON com `message` e,
em sucesso, `data`. Essas funções decodificam o corpo e retornam
(status_code, body_dict) para os testes assertarem diretamente.
"""
import requests


def _headers(token=None, extra=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def api_get(base_url, path, token=None, params=None, headers=None):
    resp = requests.get(base_url + path, headers=_headers(token, headers), params=params, timeout=30)
    return resp.status_code, resp.json()


def api_post(base_url, path, token=None, json=None, headers=None):
    resp = requests.post(base_url + path, headers=_headers(token, headers), json=json, timeout=30)
    return resp.status_code, resp.json()


def api_put(base_url, path, token=None, json=None, headers=None):
    resp = requests.put(base_url + path, headers=_headers(token, headers), json=json, timeout=30)
    return resp.status_code, resp.json()


def api_delete(base_url, path, token=None, params=None, headers=None):
    resp = requests.delete(base_url + path, headers=_headers(token, headers), params=params, timeout=30)
    return resp.status_code, resp.json()
