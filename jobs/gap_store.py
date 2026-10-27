"""Gap-only private objects; never changes screening_runs or screening_results."""

import os

import requests


class GapStore:
    def __init__(self):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.url = base + "/storage/v1/object/stocke-sector-strength/gap224/"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def get(self, path):
        response = requests.get(self.url + path, headers={**self.headers, "Cache-Control": "no-cache"}, timeout=45)
        if response.status_code == 404:
            return None
        if not response.ok:
            body = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            if body.get("statusCode") in ("404", 404) or body.get("error") == "not_found":
                return None
            raise RuntimeError(f"Gap storage read failed (HTTP {response.status_code})")
        return response.json()

    def put(self, path, value, create=False):
        response = requests.post(self.url + path,
                                 headers={**self.headers, "Content-Type": "application/json",
                                          "x-upsert": "false" if create else "true", "cache-control": "no-cache"},
                                 json=value, timeout=60)
        if create and response.status_code in (400, 409):
            try:
                body = response.json()
            except ValueError:
                body = {}
            duplicate = str(body.get("error", "")).lower() in ("duplicate", "asset already exists")
            duplicate |= str(body.get("message", "")).lower() in ("the resource already exists", "asset already exists")
            if duplicate:
                return False
        if not response.ok:
            raise RuntimeError(f"Gap storage write failed (HTTP {response.status_code})")
        return True
