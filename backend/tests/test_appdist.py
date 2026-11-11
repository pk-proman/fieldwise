import hashlib
import json
from app import config


def _publish(tmp_path, monkeypatch, code=3, min_code=2):
    apk = tmp_path / "Fieldwise-1.2.0.apk"
    apk.write_bytes(b"PK\x03\x04fake-apk")
    (tmp_path / "latest.json").write_text(json.dumps({"versionCode": code, "versionName": "1.2.0", "minVersionCode": min_code, "releaseNotes": "Fix A\nFix B",
                                                      "apk": apk.name, "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), "size": apk.stat().st_size}))
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path)


def test_nothing_published(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path)
    assert client.get("/api/app/version").json() == {"available": False}
    assert client.get("/download/fieldwise").status_code == 404
    assert "not been published" in client.get("/download").text


def test_published_release(client, tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch)
    v = client.get("/api/app/version").json()
    assert v["available"] and v["versionCode"] == 3 and v["minVersionCode"] == 2 and v["apkUrl"].endswith("/download/fieldwise")
    r = client.get("/download/fieldwise")
    assert r.status_code == 200 and r.content.startswith(b"PK") and "Fieldwise-1.2.0.apk" in r.headers["content-disposition"]
    page = client.get("/download").text
    assert v["sha256"] in page and "<svg" in page and "Allow all the time" in page


def test_latest_json_cannot_escape_releases_dir(client, tmp_path, monkeypatch):
    (tmp_path / "latest.json").write_text(json.dumps({"versionCode": 1, "versionName": "1", "apk": "../../etc/hosts", "sha256": "x", "size": 1}))
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path)
    assert client.get("/api/app/version").json() == {"available": False}
