# Tests des jalons 4.6 et 4.7 : import de playlist YouTube (validation),
# qualite de telechargement, export/import JSON de sauvegarde, et acces
# protege par code (RESONANCE_ACCESS_CODE).
import json
import os
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


def make_wav(path, seconds=1):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 44100 * seconds)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    d = tmp_path / "musique"
    d.mkdir()
    monkeypatch.setattr(app, "MUSIC_DIR", str(d))
    monkeypatch.setattr(app, "DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setattr(app, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(app, "ACCESS_CODE", "")
    app._clear_scan_cache()
    app.init_db()
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c, d, monkeypatch
    app._clear_scan_cache()


# ------------------------------------------- 4.6 : playlist YouTube (validation d'entree)

def test_playlist_endpoint_rejects_links_without_list(env):
    client, d, _ = env
    r = client.get("/api/playlist_youtube?url=https://www.youtube.com/watch?v=abc123")
    assert r.status_code == 400
    assert "list=" in r.get_json()["error"]


def test_playlist_endpoint_rejects_non_url(env):
    client, d, _ = env
    r = client.get("/api/playlist_youtube?url=ytsearch10:coucou")
    assert r.status_code == 400


# ------------------------------------------- 4.6 : qualite passee au telechargement

def test_download_youtube_passes_quality_to_job(env):
    """La qualite choisie (128/192/320) doit parvenir au worker YouTube ;
    toute valeur invalide retombe sur 192."""
    client, d, monkeypatch = env
    calls = []

    def fake_yt(job_id, track_id, title, artist, artwork_url, replace_of='', quality='192'):
        calls.append(quality)
        app._update_job(job_id, status='done', percent=100, file='x.mp3')

    monkeypatch.setattr(app, "_do_download_youtube", fake_yt)
    monkeypatch.setattr(app, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(app, "_internet_known_down", lambda max_age=60.0: False)

    r = client.post("/api/download_youtube",
                    json={"id": "dQw4w9WgXcQ", "title": "Titre", "artist": "A", "quality": 320})
    assert r.status_code == 200
    assert r.get_json()["success"] is True

    r = client.post("/api/download_youtube",
                    json={"id": "dQw4w9WgXcQ2", "title": "Titre", "artist": "A", "quality": 999})
    assert r.status_code == 200

    # le worker tourne dans un vrai thread : on attend ses deux appels
    import time
    deadline = time.time() + 5
    while time.time() < deadline and len(calls) < 2:
        time.sleep(0.05)
    assert calls == ["320", "192"]


# ------------------------------------------- 4.7a : export / import JSON

def test_backup_restore_roundtrip(env):
    """Sauvegarde puis restauration completes : favoris, playlists et
    compteurs reviennent a l'identique, meme sur une base vide."""
    client, d, _ = env
    make_wav(str(d / "a.wav"))
    make_wav(str(d / "b.wav"))
    client.post("/api/toggle_favorite", json={"path": "a.wav"})
    client.post("/api/create_playlist", json={"name": "Soiree"})
    client.post("/api/toggle_playlist_song", json={"playlist": "Soiree", "path": "a.wav"})
    client.post("/api/toggle_playlist_song", json={"playlist": "Soiree", "path": "b.wav"})
    client.post("/api/play_count", json={"path": "a.wav"})
    client.post("/api/play_count", json={"path": "a.wav"})

    backup = client.get("/api/backup").get_json()
    assert backup["app"] == "RESONANCE"
    assert set(backup["favorites"]) == {"a.wav"}
    assert backup["playlists"] == {"Soiree": ["a.wav", "b.wav"]}
    assert backup["play_counts"]["a.wav"]["plays"] == 2

    # nouveau serveur : base vide
    conn = app.get_db()
    for table in ("favorites", "playlist_songs", "playlists", "play_counts"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    state = client.get("/api/state").get_json()
    assert state["favorites"] == [] and state["playlists"] == {}

    r = client.post("/api/restore", json=backup)
    assert r.status_code == 200
    assert r.get_json()["success"] is True

    state = client.get("/api/state").get_json()
    assert state["favorites"] == ["a.wav"]
    assert state["playlists"] == {"Soiree": ["a.wav", "b.wav"]}
    assert state["play_counts"]["a.wav"]["plays"] == 2


def test_restore_rejects_foreign_file(env):
    client, d, _ = env
    r = client.post("/api/restore", json={"app": "AUTRE", "favorites": []})
    assert r.status_code == 400
    r = client.post("/api/restore", json="pas un dict")
    assert r.status_code == 400


def test_restore_accepts_backup_without_counts(env):
    """Une sauvegarde anterieure aux statistiques reste importable."""
    client, d, _ = env
    r = client.post("/api/restore", json={
        "app": "RESONANCE", "favorites": ["x.wav"],
        "playlists": {"P": ["x.wav"]}})
    assert r.status_code == 200
    state = client.get("/api/state").get_json()
    assert state["favorites"] == ["x.wav"]


# ------------------------------------------- 4.7b : acces protege par code

def test_no_code_means_open_access(env):
    client, d, _ = env
    assert client.get("/api/state").status_code == 200
    assert client.get("/").status_code == 200


def test_code_locks_everything_until_unlock(env):
    client, d, monkeypatch = env
    monkeypatch.setattr(app, "ACCESS_CODE", "secret-1234")

    # API protegee, page protegee
    r = client.get("/api/state")
    assert r.status_code == 401
    assert r.get_json()["locked"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert b"Code" in page.data or b"code" in page.data

    # mauvais code : refuse (et freine)
    assert client.post("/api/unlock", json={"code": "faux"}).status_code == 403
    # l'acces reste ferme
    assert client.get("/api/state").status_code == 401

    # bon code : cookie pose, tout se reouvre
    r = client.post("/api/unlock", json={"code": "secret-1234"})
    assert r.status_code == 200
    assert client.get("/api/state").status_code == 200
    assert client.get("/").status_code == 200
    # et l'etat de la page ne fuit plus la page de deverrouillage
    assert b"RESONANCE" in client.get("/").data
    assert client.post("/api/unlock", json={"code": ""}).status_code == 403


def test_unlock_endpoint_open_when_no_code(env):
    client, d, _ = env
    r = client.post("/api/unlock", json={"code": "n'importe"})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
