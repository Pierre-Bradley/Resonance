# Tests des deux ameliorations 3.1 (cache de scan + ETag) et 3.2 (SW plafone).
# Genere de vrais fichiers audio, demarre le serveur Flask de app.py et
# verifie les comportements par de vraies requetes HTTP.
import json
import os
import re
import struct
import sys
import time
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOAD_DIR = APP_DIR


def make_wav(path, seconds=1):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 44100 * seconds)


@pytest.fixture()
def music_dir(tmp_path, monkeypatch):
    d = tmp_path / "musique"
    d.mkdir()
    monkeypatch.setattr(app, "MUSIC_DIR", str(d))
    app._clear_scan_cache()
    yield d
    app._clear_scan_cache()


@pytest.fixture()
def client(tmp_path, monkeypatch, music_dir):
    monkeypatch.setattr(app, "DB_FILE", str(tmp_path / "test.db"))
    app.init_db()   # cree la base au nouvel emplacement avant les requetes
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c


# ---------------------------------------------------------------- 3.1 : scan

def test_scan_finds_wav(music_dir):
    make_wav(str(music_dir / "a.wav"))
    songs = app.get_all_songs(str(music_dir))
    assert len(songs) == 1
    assert songs[0]["name"] == "a.wav"
    assert songs[0]["duration"] == 1
    assert "size" in songs[0] and songs[0]["size"] > 0


def test_scan_cache_reuses_unchanged_files(music_dir, monkeypatch):
    """Le 2e scan ne doit PAS rouvrir les fichiers : les metadonnees viennent
    du cache (compteur d'appels a extract_metadata)."""
    make_wav(str(music_dir / "a.wav"))
    calls = []
    real = app.extract_metadata
    monkeypatch.setattr(app, "extract_metadata",
                        lambda p: (calls.append(p), real(p))[1])
    app.get_all_songs(str(music_dir))
    n_first = len(calls)
    assert n_first == 1
    songs2 = app.get_all_songs(str(music_dir))
    assert len(songs2) == 1
    assert len(calls) == n_first   # aucun relekture au 2e sondage


def test_scan_cache_invalidated_on_change(music_dir):
    make_wav(str(music_dir / "a.wav"))
    s1 = app.get_all_songs(str(music_dir))
    # modification du fichier (mtime + taille changent)
    time.sleep(0.01)
    make_wav(str(music_dir / "a.wav"), seconds=2)
    s2 = app.get_all_songs(str(music_dir))
    assert s2[0]["duration"] == 2   # bien re-analyse, pas la valeur cachee
    assert s1[0]["duration"] == 1


def test_scan_cache_drops_deleted_files(music_dir):
    make_wav(str(music_dir / "a.wav"))
    make_wav(str(music_dir / "b.wav"))
    assert len(app.get_all_songs(str(music_dir))) == 2
    os.remove(music_dir / "a.wav")
    songs = app.get_all_songs(str(music_dir))
    assert [s["name"] for s in songs] == ["b.wav"]


def test_scan_new_file_after_first_scan(music_dir):
    make_wav(str(music_dir / "a.wav"))
    app.get_all_songs(str(music_dir))
    make_wav(str(music_dir / "nouveau.wav"))
    songs = app.get_all_songs(str(music_dir))
    assert sorted(s["name"] for s in songs) == ["a.wav", "nouveau.wav"]


# ------------------------------------------------- 3.1 : ETag / 304 / state

def test_state_returns_etag_and_304(client, music_dir):
    make_wav(str(music_dir / "a.wav"))
    r1 = client.get("/api/state")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    assert etag
    data = r1.get_json()
    assert data["songs"][0]["name"] == "a.wav"
    # 2e requete avec If-None-Match -> 304, corps vide
    r2 = client.get("/api/state", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.get_data() == b""
    # etag different -> 200 complet
    r3 = client.get("/api/state", headers={"If-None-Match": '"faitux"'})
    assert r3.status_code == 200
    assert r3.get_json()["songs"][0]["name"] == "a.wav"


def test_state_etag_changes_when_library_changes(client, music_dir):
    make_wav(str(music_dir / "a.wav"))
    etag1 = client.get("/api/state").headers["ETag"]
    make_wav(str(music_dir / "b.wav"))
    etag2 = client.get("/api/state").headers["ETag"]
    assert etag1 != etag2


def test_state_etag_changes_on_favorite(client, music_dir):
    make_wav(str(music_dir / "a.wav"))
    etag1 = client.get("/api/state").headers["ETag"]
    client.post("/api/toggle_favorite", json={"path": "a.wav"})
    r = client.get("/api/state")
    etag2 = r.headers["ETag"]
    assert etag1 != etag2
    assert "a.wav" in r.get_json()["favorites"]
    # et le 304 fonctionne avec le nouvel etag
    r304 = client.get("/api/state", headers={"If-None-Match": etag2})
    assert r304.status_code == 304


# ---------------------------------------------------------------- 3.2 : SW

def test_sw_v9_caches_and_limits():
    m = re.search(r'SW_CODE = """(.*?)"""', open(os.path.join(DOWNLOAD_DIR, "app.py"), encoding="utf-8").read(), re.S)
    sw = m.group(1)
    assert "resonance-static-v9" in sw
    assert "resonance-media-v9" in sw
    assert "resonance-v8" not in sw
    # plafond + eviction present
    assert "MEDIA_MAX_BYTES" in sw and "trimMediaCache" in sw
    # les streams ne sont plus mis en cache automatiquement : plus de
    # cache.put sur /stream/ dans le handler fetch
    fetch_block = sw[sw.index("self.addEventListener('fetch'"):]
    assert "cache.put" not in fetch_block
    # pin/unpin + estimation cote client
    assert "PIN_TRACK" in sw and "UNPIN_TRACK" in sw and "CLEAR_MEDIA_CACHE" in sw


def test_client_pin_and_storage_code():
    html = open(os.path.join(DOWNLOAD_DIR, "templates", "index.html"), encoding="utf-8").read()
    # jalon 3.5 : la punaise par ligne cede sa place au bouton supprimer
    # (les titres telecharges restent de toute facon accessibles hors ligne).
    # Le stockage hors ligne reste gere : liste epingree locale + UNPIN au SW.
    assert "song-actions" in html
    song_actions = html[html.find("song-actions"):]
    assert "fa-thumbtack" not in song_actions
    assert "fa-trash" in song_actions and "deleteSong(" in song_actions
    assert "/api/delete_song" in html
    assert "UNPIN_TRACK" in html
    assert "navigator.storage.estimate" in html and "showStorageInfo" in html
    assert "If-None-Match" in html and "stateETag" in html
    assert "res.status === 304" in html
    # la vue Favoris ne change pas : le coeur reste le coeur
    assert "toggleFavorite" in html


def test_sw_js_and_client_js_syntax(tmp_path):
    src = open(os.path.join(DOWNLOAD_DIR, "app.py"), encoding="utf-8").read()
    sw = re.search(r'SW_CODE = """(.*?)"""', src, re.S).group(1)
    p1 = tmp_path / "sw.js"
    p1.write_text(sw, encoding="utf-8")
    html = open(os.path.join(DOWNLOAD_DIR, "templates", "index.html"), encoding="utf-8").read()
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    p2 = tmp_path / "client.js"
    p2.write_text(js, encoding="utf-8")
    # node --check si disponible, sinon skip silencieux (couvert ailleurs)
    import shutil
    node = shutil.which("node")
    if node:
        import subprocess
        for p in (p1, p2):
            r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
