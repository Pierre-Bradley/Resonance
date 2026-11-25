# Tests du jalon 3.5 (robustesse) + bouton supprimer : WAL SQLite, suppression
# de titre (fichier + references), remplacement de doublon cote serveur,
# hors ligne automatique des telechargements (table downloaded_files).
import json
import time
import os
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    app._clear_scan_cache()
    app.init_db()
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c, d
    app._clear_scan_cache()


# ------------------------------------------------------------- 3.5c : WAL

def test_sqlite_wal_mode(env):
    """init_db doit activer le journal WAL (moins de verrous entre les 2
    processus gunicorn) ; busy_timeout est pose sur chaque connexion."""
    conn = app.get_db()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert mode == "wal"
    assert busy == 3000


# -------------------------------------------------- bouton supprimer (3.5)

def test_delete_song_removes_file_and_references(env):
    client, d = env
    make_wav(str(d / "a.wav"))
    rel = "a.wav"
    client.post("/api/toggle_favorite", json={"path": rel})
    client.post("/api/create_playlist", json={"name": "Soiree"})
    client.post("/api/toggle_playlist_song", json={"playlist": "Soiree", "path": rel})

    r = client.post("/api/delete_song", json={"path": rel})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    assert not (d / "a.wav").exists()

    songs = app.get_all_songs(str(d))
    assert len(songs) == 0

    conn = app.get_db()
    favs = conn.execute("SELECT COUNT(*) c FROM favorites WHERE path = ?", (rel,)).fetchone()["c"]
    in_pl = conn.execute("SELECT COUNT(*) c FROM playlist_songs WHERE song_path = ?", (rel,)).fetchone()["c"]
    conn.close()
    assert favs == 0
    assert in_pl == 0


def test_delete_song_missing_or_escaping_path(env):
    client, d = env
    # fichier inexistant
    r = client.post("/api/delete_song", json={"path": "fantome.wav"})
    assert r.status_code == 404
    # sortie du dossier de musique : refuse
    (d / ".." ).exists()   # noop, clarté
    r = client.post("/api/delete_song", json={"path": "../etc/passwd"})
    assert r.status_code == 404
    r = client.post("/api/delete_song", json={"path": ""})
    assert r.status_code == 400


# --------------------------------- remplacement de doublon (3.5, serveur)

def test_delete_replaced_file_removes_old_only(tmp_path, monkeypatch):
    """Apres un telechargement en mode 'remplacer', l'ancien fichier part —
    jamais un chemin exterieur (traversal refuse)."""
    monkeypatch.setattr(app, "ONLINE_DOWNLOAD_DIR", str(tmp_path))
    old = tmp_path / "Artiste - Titre.mp3"
    other = tmp_path / "Autre.mp3"
    outside = tmp_path.parent / "hors_dossier.mp3"
    for p in (old, other, outside):
        p.write_bytes(b"x")
    app._delete_replaced_file("Artiste - Titre.mp3", str(tmp_path / "new.mp3"))
    assert not old.exists()
    assert other.exists()
    # traversal : refuse, le fichier exterieur reste
    app._delete_replaced_file("../hors_dossier.mp3", str(tmp_path / "new.mp3"))
    assert outside.exists()
    # jamais le fichier tout juste telecharge
    keep = tmp_path / "garde.mp3"
    keep.write_bytes(b"x")
    app._delete_replaced_file("garde.mp3", str(keep))
    assert keep.exists()


# ------------------------ hors ligne automatique des telechargements (3.5+)

def test_downloaded_flag_marks_app_downloads_only(env):
    client, d = env
    make_wav(str(d / "dl.wav"))
    make_wav(str(d / "local.wav"))
    app._record_downloaded_file("dl.wav")
    songs = {s["name"]: s for s in app.get_all_songs(str(d))}
    assert songs["dl.wav"]["downloaded"] is True
    assert songs["local.wav"]["downloaded"] is False


def test_backfill_downloaded_files_from_jobs(env, tmp_path, monkeypatch):
    """Les telechargements faits avant l'introduction de la table sont
    retrouves via le journal des taches persistant."""
    client, d = env
    make_wav(str(d / "old.wav"))
    monkeypatch.setattr(app, "JOBS_FILE", str(tmp_path / "jobs.json"))
    (tmp_path / "jobs.json").write_text(json.dumps({
        "j1": {"status": "done", "file": "old.wav"},
        "j2": {"status": "error", "file": "rate.wav"},
    }), encoding="utf-8")
    app._backfill_downloaded_files()
    songs = {s["name"]: s for s in app.get_all_songs(str(d))}
    assert songs["old.wav"]["downloaded"] is True


def test_delete_song_forgets_downloaded_flag(env):
    client, d = env
    make_wav(str(d / "parti.wav"))
    app._record_downloaded_file("parti.wav")
    r = client.post("/api/delete_song", json={"path": "parti.wav"})
    assert r.status_code == 200
    assert "parti.wav" not in app._downloaded_files_set()


# ---------------- sonde reseau non bloquante + gardes sans Internet (3.5+)

def test_check_internet_returns_cached_state_instantly(env):
    """_check_internet ne fait JAMAIS de reseau : elle renvoie l'etat dernier
    connu et confie la sonde a un thread de fond (worker jamais bloque)."""
    app._net_cache.update({"ts": time.time(), "online": True,
                           "audius_ok": True, "youtube_ok": True})
    state = app._check_internet()
    assert state["online"] is True
    assert state["ts"] <= time.time()


def test_internet_known_down_gate(env, monkeypatch):
    monkeypatch.setattr(app, "_maybe_start_probe", lambda: None)
    app._net_cache.update({"ts": time.time(), "online": False,
                           "audius_ok": False, "youtube_ok": False})
    assert app._internet_known_down() is True
    # etat perime : le garde se desactive (une sonde est relancee en fond)
    app._net_cache["ts"] = time.time() - 120
    assert app._internet_known_down() is False
    # etat inconnu (aucune sonde aboutie) : pas de garde
    app._net_cache.update({"ts": time.time(), "online": None})
    assert app._internet_known_down() is False


def test_online_endpoints_fail_fast_without_internet(env):
    """Serveur sans Internet : les fonctions en ligne rendent une erreur
    claire aussitot, au lieu de pendre sur le DNS et d'affamer les workers."""
    client, d = env
    app._net_cache.update({"ts": time.time(), "online": False,
                           "audius_ok": False, "youtube_ok": False})
    for url in ("/api/search_online?q=test", "/api/trending_online",
                "/api/search_youtube?q=test", "/api/stream_online/abc123"):
        r = client.get(url)
        assert r.status_code == 503, url
        assert "Internet" in r.get_json()["error"]
    r = client.post("/api/lyrics", json={"title": "x", "artist": "y"})
    assert r.status_code == 503
    r = client.post("/api/download_online", json={"id": "abc"})
    assert r.status_code == 503


def test_local_endpoints_unaffected_without_internet(env):
    """Le coeur de l'application (bibliotheque, flux, paroles en cache) ne
    depend pas d'Internet : tout continue de repondre normalement."""
    client, d = env
    make_wav(str(d / "local.wav"))
    app._net_cache.update({"ts": time.time(), "online": False,
                           "audius_ok": False, "youtube_ok": False})
    r = client.get("/api/state")
    assert r.status_code == 200
    assert any(s["name"] == "local.wav" for s in r.get_json()["songs"])
    r = client.get("/stream/local.wav")
    assert r.status_code == 200
