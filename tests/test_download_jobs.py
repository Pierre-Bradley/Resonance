# Tests du jalon 3.3 : telechargements en arriere-plan (taches + progression).
# Le worker reseau est remplace par un stub ; on verifie le cycle complet
# creation -> progression -> statut via de vraies requetes HTTP.
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


@pytest.fixture()
def jobs_env(tmp_path, monkeypatch):
    """Jobs isoles par test : fichier JSON dans tmp_path, worker reseaux stubbe."""
    monkeypatch.setattr(app, "JOBS_FILE", str(tmp_path / "download_jobs.json"))
    jobs_done = []

    def fake_audius(job_id, track_id, title, artist, artwork_url, replace_of=''):
        app._update_job(job_id, status='downloading', percent=50, speed='1.0 Mo/s')
        time.sleep(0.05)
        app._update_job(job_id, status='done', percent=100,
                        file=f"{artist} - {title}.mp3", speed='', eta='')
        jobs_done.append(job_id)

    monkeypatch.setattr(app, "_do_download_audius", fake_audius)
    app.app.config["TESTING"] = True
    yield tmp_path


@pytest.fixture()
def client(jobs_env):
    with app.app.test_client() as c:
        yield c


def _wait_job(client, job_id, status, timeout=5.0):
    """Le worker tourne dans un vrai thread : on attend le statut voulu."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/download_status/{job_id}")
        job = r.get_json().get("job") or {}
        if job.get("status") == status:
            return job
        time.sleep(0.05)
    return job


# ------------------------------------------------- creation immediatement

def test_download_online_returns_job_id_immediately(client):
    r = client.post("/api/download_online",
                    json={"id": "abc123", "title": "Titre", "artist": "Artiste"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    assert data["job_id"]


def test_download_online_rejects_invalid_id(client):
    r = client.post("/api/download_online",
                    json={"id": "../evil", "title": "x", "artist": "y"})
    assert r.status_code == 400
    assert r.get_json()["success"] is False


# ------------------------------------------------------- cycle de la tache

def test_job_progresses_then_done(client):
    r = client.post("/api/download_online",
                    json={"id": "track1", "title": "Chanson", "artist": "Artiste"})
    job_id = r.get_json()["job_id"]
    job = _wait_job(client, job_id, "done")
    assert job["percent"] == 100
    assert job["file"] == "Artiste - Chanson.mp3"


def test_status_endpoint_lists_all_jobs(client):
    r = client.post("/api/download_online",
                    json={"id": "track2", "title": "T2", "artist": "A2"})
    job_id = r.get_json()["job_id"]
    _wait_job(client, job_id, "done")
    jobs = client.get("/api/download_status").get_json()["jobs"]
    assert any(j["id"] == job_id for j in jobs)


def test_unknown_job_returns_404(client):
    assert client.get("/api/download_status/inconnu123").status_code == 404


def test_same_track_deduplicated(client):
    payload = {"id": "track3", "title": "T3", "artist": "A3"}
    r1 = client.post("/api/download_online", json=payload)
    r2 = client.post("/api/download_online", json=payload)
    assert r1.get_json()["job_id"] == r2.get_json()["job_id"]


def test_youtube_route_creates_job(tmp_path, client, monkeypatch):
    def fake_yt(job_id, track_id, title, artist, artwork_url, replace_of=''):
        app._update_job(job_id, status='done', percent=100,
                        file=f"{artist} - {title}.mp3", speed='', eta='')

    monkeypatch.setattr(app, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(app, "_do_download_youtube", fake_yt)
    r = client.post("/api/download_youtube",
                    json={"id": "dQw4w9WgXcQ", "title": "T", "artist": "YouTube"})
    assert r.status_code == 200
    job_id = r.get_json()["job_id"]
    job = _wait_job(client, job_id, "done")
    assert job["file"] == "YouTube - T.mp3"


# ------------------------------------------- persistance + tache orpheline

def test_jobs_persisted_to_shared_file(jobs_env):
    """L'etat doit vivre dans le fichier JSON partage entre les 2 processus
    gunicorn : c'est ce qui rend les taches visibles d'un worker a l'autre."""
    app._create_job("audius", "audius:persist1", "T", "A")
    with open(app.JOBS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert any(j["target"] == "audius:persist1" for j in data.values())


def test_orphan_job_flagged_as_interrupted(tmp_path, client, monkeypatch):
    """Tache 'downloading' d'un processus mort et sans maj depuis longtemps :
    le statut doit passer en erreur avec un message clair, pas rester bloquee."""
    jobs_file = tmp_path / "download_jobs.json"
    dead = {
        "deadjob1": {
            "id": "deadjob1", "kind": "audius", "target": "audius:dead",
            "title": "T", "artist": "A", "status": "downloading",
            "percent": 30, "speed": "", "eta": "", "error": "", "file": "",
            "created": time.time() - 1000, "updated": time.time() - 1000,
            "pid": 99999999,
        }
    }
    with open(jobs_file, "w", encoding="utf-8") as f:
        json.dump(dead, f)
    job = client.get("/api/download_status/deadjob1").get_json()["job"]
    assert job["status"] == "error"
    assert "interrompu" in job["error"].lower()


def test_live_process_job_not_flagged(tmp_path, client):
    """Meme tache ancienne : si le PID est vivant (le notre), pas de faux
    'interrompu' — un gros fichier lent peut ne rien pousser pendant un moment."""
    job, _ = app._create_job("audius", "audius:slow", "T", "A")
    app._update_job(job["id"], status="downloading",
                    updated=time.time() - 1000)
    view = client.get(f"/api/download_status/{job['id']}").get_json()["job"]
    assert view["status"] == "downloading"
