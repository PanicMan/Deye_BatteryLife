"""Minimal DeyeCloud Open API client (stdlib only).

Credentials come from a secrets dict (secrets.json) or, as fallback, from env vars.
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("DEYE_BASE", "https://eu1-developer.deyecloud.com")


def set_base(url):
    global BASE
    BASE = url.rstrip("/")


def call(path, body=None, token=None, query=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get(path, token):
    req = urllib.request.Request(BASE + path, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def business_token(secrets=None):
    """Org-scoped token (companyId required, otherwise list_stations returns nothing)."""
    if secrets is None:
        e = os.environ
        secrets = {"app_id": e["DEYE_APP_ID"], "app_secret": e["DEYE_APP_SECRET"], "email": e["DEYE_EMAIL"],
                   "password_sha256": e["DEYE_PW_SHA256"], "company_id": int(e["DEYE_COMPANY_ID"])}
    if secrets.get("base_url"):
        set_base(secrets["base_url"])
    res = call("/v1.0/account/token",
               {"appSecret": secrets["app_secret"], "email": secrets["email"],
                "password": secrets["password_sha256"], "companyId": int(secrets["company_id"])},
               query={"appId": secrets["app_id"]})
    if not res.get("success"):
        raise RuntimeError("token request failed: %s" % {k: res.get(k) for k in ("code", "msg")})
    return res["accessToken"]
