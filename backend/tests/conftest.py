import os
import tempfile
import pytest

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["NETGUARD_DISABLE"] = "1"
os.environ["RATELIMIT_DISABLE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def signup(client, company, email="owner@x.com", name="Owner One", country="India"):
    r = client.post("/api/auth/signup", json={"company": company, "name": name, "email": email, "password": "Passw0rd!x", "country": country})
    assert r.status_code == 200, r.text
    d = r.json()
    hdr = {"Authorization": f"Bearer {d['token']}"}
    client.put("/api/settings", headers=hdr, json={"require_tracking_consent": False})  # most tests aren't about the consent notice
    return d["workspace"], hdr


def login(client, ws, email, pw="Passw0rd!x"):
    r = client.post("/api/auth/login", json={"workspace": ws, "email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}
