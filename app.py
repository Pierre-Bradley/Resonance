import re
import os
import sqlite3
import requests
import base64
from flask import Flask, render_template, jsonify, send_file, request, Response, stream_with_context
from werkzeug.utils import secure_filename

# =============================================================================
# MUTAGEN
# =============================================================================
try:
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
    from mutagen.wave import WAVE
    from mutagen.id3 import ID3, APIC, TPE1, TALB, TCON, TIT2
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("=" * 60)
    print("WARNING: mutagen n'est pas installe.")
    print("Installez-le avec:  pip install mutagen")
    print("=" * 60)

app = Flask(__name__)

MUSIC_DIR = os.path.join(os.path.expanduser('~'), 'Musique')
DB_FILE = 'resonance.db'
OLD_DATA_FILE = 'resonance_data.json'

# =============================================================================
# PATH TRAVERSAL PROTECTION
# =============================================================================

def _safe_path(subpath):
    safe_path = os.path.normpath(os.path.join(MUSIC_DIR, subpath))
    abs_music_dir = os.path.abspath(MUSIC_DIR)
    abs_safe_path = os.path.abspath(safe_path)
    if not abs_safe_path.startswith(abs_music_dir + os.sep) and abs_safe_path != abs_music_dir:
        return None
    if not os.path.exists(abs_safe_path):
        return None
    return abs_safe_path


# =============================================================================
# SQLITE
# =============================================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favorites (path TEXT PRIMARY KEY)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlists (name TEXT PRIMARY KEY)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlist_songs (
            playlist_name TEXT NOT NULL,
            song_path TEXT NOT NULL,
            PRIMARY KEY (playlist_name, song_path),
            FOREIGN KEY (playlist_name) REFERENCES playlists(name) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()
    migrate_from_json()


def migrate_from_json():
    import json
    if not os.path.exists(OLD_DATA_FILE):
        return
    try:
        with open(OLD_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    conn = get_db()
    cursor = conn.cursor()
    for path in data.get('favorites', []):
        cursor.execute('INSERT OR IGNORE INTO favorites (path) VALUES (?)', (path,))
    for pl_name, songs in data.get('playlists', {}).items():
        cursor.execute('INSERT OR IGNORE INTO playlists (name) VALUES (?)', (pl_name,))
        for song_path in songs:
            cursor.execute(
                'INSERT OR IGNORE INTO playlist_songs (playlist_name, song_path) VALUES (?, ?)',
                (pl_name, song_path)
            )
    conn.commit()
    conn.close()
    os.rename(OLD_DATA_FILE, OLD_DATA_FILE + '.backup')
    print(f"[MIGRATION] Donnees migrees depuis {OLD_DATA_FILE} vers SQLite.")


def get_favorites():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT path FROM favorites')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


def toggle_favorite_db(path):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM favorites WHERE path = ?', (path,))
    exists = cursor.fetchone()
    if exists:
        cursor.execute('DELETE FROM favorites WHERE path = ?', (path,))
    else:
        cursor.execute('INSERT INTO favorites (path) VALUES (?)', (path,))
    conn.commit()
    conn.close()


def get_playlists():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM playlists')
    playlists = {r[0]: [] for r in cursor.fetchall()}
    cursor.execute('SELECT playlist_name, song_path FROM playlist_songs')
    for row in cursor.fetchall():
        pl_name = row[0]
        if pl_name in playlists:
            playlists[pl_name].append(row[1])
    conn.close()
    return playlists


def create_playlist_db(name):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO playlists (name) VALUES (?)', (name,))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def delete_playlist_db(name):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM playlists WHERE name = ?', (name,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def toggle_playlist_song_db(playlist_name, song_path):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT 1 FROM playlist_songs WHERE playlist_name = ? AND song_path = ?',
        (playlist_name, song_path)
    )
    exists = cursor.fetchone()
    if exists:
        cursor.execute(
            'DELETE FROM playlist_songs WHERE playlist_name = ? AND song_path = ?',
            (playlist_name, song_path)
        )
    else:
        cursor.execute(
            'INSERT INTO playlist_songs (playlist_name, song_path) VALUES (?, ?)',
            (playlist_name, song_path)
        )
    conn.commit()
    conn.close()


# =============================================================================
# METADONNEES — LECTURE
# =============================================================================

def extract_metadata(file_path):
    metadata = {
        "artist": None, "album": None, "genre": None,
        "title": None, "duration": 0, "has_cover": False
    }
    if not MUTAGEN_AVAILABLE:
        return metadata
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.mp3':
            audio = MP3(file_path)
            if audio.tags:
                metadata["artist"] = _safe_tag(audio.tags, 'TPE1')
                metadata["album"]  = _safe_tag(audio.tags, 'TALB')
                metadata["genre"]  = _safe_tag(audio.tags, 'TCON')
                metadata["title"]  = _safe_tag(audio.tags, 'TIT2')
                for tag in audio.tags.values():
                    if isinstance(tag, APIC):
                        metadata["has_cover"] = True
                        break
            if audio.info:
                metadata["duration"] = int(audio.info.length)
        elif ext == '.flac':
            audio = FLAC(file_path)
            if audio.tags:
                metadata["artist"] = _safe_tag_list(audio.tags, 'artist')
                metadata["album"]  = _safe_tag_list(audio.tags, 'album')
                metadata["genre"]  = _safe_tag_list(audio.tags, 'genre')
                metadata["title"]  = _safe_tag_list(audio.tags, 'title')
            if audio.pictures:
                metadata["has_cover"] = True
            if audio.info:
                metadata["duration"] = int(audio.info.length)
        elif ext in ('.m4a', '.mp4', '.m4p'):
            audio = MP4(file_path)
            if audio.tags:
                metadata["artist"] = _safe_tag_mp4(audio.tags, '\xa9ART')
                metadata["album"]  = _safe_tag_mp4(audio.tags, '\xa9alb')
                metadata["genre"]  = _safe_tag_mp4(audio.tags, '\xa9gen')
                metadata["title"]  = _safe_tag_mp4(audio.tags, '\xa9nam')
            if audio.tags and 'covr' in audio.tags:
                metadata["has_cover"] = True
            if audio.info:
                metadata["duration"] = int(audio.info.length)
        elif ext == '.ogg':
            audio = OggVorbis(file_path)
            if audio.tags:
                metadata["artist"] = _safe_tag_list(audio.tags, 'artist')
                metadata["album"]  = _safe_tag_list(audio.tags, 'album')
                metadata["genre"]  = _safe_tag_list(audio.tags, 'genre')
                metadata["title"]  = _safe_tag_list(audio.tags, 'title')
            if audio.info:
                metadata["duration"] = int(audio.info.length)
        elif ext == '.wav':
            audio = WAVE(file_path)
            if audio.info:
                metadata["duration"] = int(audio.info.length)
    except Exception:
        pass
    return metadata


def _safe_tag(tags, key):
    val = tags.get(key)
    return str(val) if val else None


def _safe_tag_list(tags, key):
    val = tags.get(key)
    if val and len(val) > 0:
        return val[0]
    return None


def _safe_tag_mp4(tags, key):
    val = tags.get(key)
    if val and len(val) > 0:
        return str(val[0])
    return None


def get_cover_image(file_path):
    if not MUTAGEN_AVAILABLE:
        return None, None
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.mp3':
            audio = MP3(file_path)
            if audio.tags:
                for tag in audio.tags.values():
                    if isinstance(tag, APIC):
                        return tag.data, tag.mime
        elif ext == '.flac':
            audio = FLAC(file_path)
            if audio.pictures:
                pic = audio.pictures[0]
                return pic.data, pic.mime
        elif ext in ('.m4a', '.mp4', '.m4p'):
            audio = MP4(file_path)
            if audio.tags and 'covr' in audio.tags:
                covers = audio.tags['covr']
                if covers:
                    return covers[0], 'image/jpeg'
    except Exception:
        pass
    return None, None


# =============================================================================
# METADONNEES — ECRITURE (EDITEUR INTEGRE)
# =============================================================================

def save_metadata(file_path, title, artist, album, genre):
    """Sauvegarde les metadonnees ID3 dans le fichier audio."""
    if not MUTAGEN_AVAILABLE:
        return False
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.mp3':
            audio = MP3(file_path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags["TIT2"] = TIT2(encoding=3, text=title or "")
            audio.tags["TPE1"] = TPE1(encoding=3, text=artist or "")
            audio.tags["TALB"] = TALB(encoding=3, text=album or "")
            audio.tags["TCON"] = TCON(encoding=3, text=genre or "")
            audio.save()
            return True
        elif ext == '.flac':
            audio = FLAC(file_path)
            audio.tags["title"] = title or ""
            audio.tags["artist"] = artist or ""
            audio.tags["album"] = album or ""
            audio.tags["genre"] = genre or ""
            audio.save()
            return True
        elif ext in ('.m4a', '.mp4', '.m4p'):
            audio = MP4(file_path)
            audio.tags["\xa9nam"] = [title or ""]
            audio.tags["\xa9ART"] = [artist or ""]
            audio.tags["\xa9alb"] = [album or ""]
            audio.tags["\xa9gen"] = [genre or ""]
            audio.save()
            return True
        elif ext == '.ogg':
            audio = OggVorbis(file_path)
            audio.tags["title"] = title or ""
            audio.tags["artist"] = artist or ""
            audio.tags["album"] = album or ""
            audio.tags["genre"] = genre or ""
            audio.save()
            return True
    except Exception as e:
        print(f"[ERREUR] Sauvegarde metadata: {e}")
    return False


# =============================================================================
# SCAN
# =============================================================================

def get_all_songs(directory):
    music_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.mp3', '.wav', '.ogg', '.flac', '.m4a')):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, directory).replace('\\', '/')
                mtime = os.path.getmtime(full_path)
                meta = extract_metadata(full_path)
                display_title = meta["title"] or os.path.splitext(file)[0]
                music_files.append({
                    "name": file,
                    "path": rel_path,
                    "mtime": mtime,
                    "title": display_title,
                    "artist": meta["artist"] or "Artiste inconnu",
                    "album": meta["album"] or "Album inconnu",
                    "genre": meta["genre"] or "Genre inconnu",
                    "duration": meta["duration"],
                    "has_cover": meta["has_cover"]
                })
    return music_files


# =============================================================================
# MANIFEST & SERVICE WORKER (PWA)
# =============================================================================

MANIFEST = {
    "name": "RESONANCE",
    "short_name": "RESONANCE",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0e1a",
    "theme_color": "#c9a227",
    "icons": [
        {"src": "/static/img/logo1.png", "sizes": "512x512", "type": "image/png"}
    ]
}

SW_CODE = """
const CACHE_NAME = 'resonance-v5';
const STATIC_URLS = ['/'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_URLS)));
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'GET') return;

    // Couvertures + streams locaux : cache-first (ecoute hors-ligne)
    if (url.pathname.startsWith('/api/cover/') || url.pathname.startsWith('/stream/')) {
        event.respondWith(
            caches.match(event.request).then((cached) => cached || fetch(event.request).then((resp) => {
                if (resp.status === 200) {
                    const clone = resp.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                }
                return resp;
            }))
        );
        return;
    }
    // Etat de l'app : reseau d'abord
    if (url.pathname === '/api/state') {
        event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
        return;
    }
    // Autres API (recherche/stream en ligne...) : ne pas intercepter
    if (url.pathname.startsWith('/api/')) return;

    // Page et statique : cache-first
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
"""


# =============================================================================
# ROUTES FLASK
# =============================================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/manifest.json')
def manifest():
    return jsonify(MANIFEST)


@app.route('/sw.js')
def service_worker():
    return Response(SW_CODE, mimetype='application/javascript')


@app.route('/api/state')
def get_state():
    songs = get_all_songs(MUSIC_DIR)
    return jsonify({
        "songs": songs,
        "favorites": get_favorites(),
        "playlists": get_playlists()
    })


@app.route('/api/cover/<path:subpath>')
def get_cover(subpath):
    safe = _safe_path(subpath)
    if safe is None:
        return jsonify({"error": "Acces refuse"}), 403
    cover_data, mime = get_cover_image(safe)
    if cover_data:
        mime = mime or 'image/jpeg'
        return Response(cover_data, mimetype=mime)
    return jsonify({"error": "Pas de pochette"}), 404


@app.route('/api/metadata/<path:subpath>')
def get_metadata(subpath):
    safe = _safe_path(subpath)
    if safe is None:
        return jsonify({"error": "Acces refuse"}), 403
    meta = extract_metadata(safe)
    return jsonify({
        "success": True,
        "path": subpath,
        "title": meta["title"] or os.path.splitext(os.path.basename(subpath))[0],
        "artist": meta["artist"] or "Artiste inconnu",
        "album": meta["album"] or "Album inconnu",
        "genre": meta["genre"] or "Genre inconnu",
        "duration": meta["duration"],
        "has_cover": meta["has_cover"]
    })


@app.route('/api/toggle_favorite', methods=['POST'])
def toggle_favorite():
    path = request.json.get('path')
    if not path:
        return jsonify({"success": False}), 400
    toggle_favorite_db(path)
    return jsonify({"success": True})


@app.route('/api/create_playlist', methods=['POST'])
def create_playlist():
    name = request.json.get('name', '').strip()
    if not name:
        return jsonify({"success": False}), 400
    if create_playlist_db(name):
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@app.route('/api/delete_playlist', methods=['POST'])
def delete_playlist():
    name = request.json.get('name')
    if delete_playlist_db(name):
        return jsonify({"success": True})
    return jsonify({"success": False}), 404


@app.route('/api/toggle_playlist_song', methods=['POST'])
def toggle_playlist_song():
    playlist_name = request.json.get('playlist')
    song_path = request.json.get('path')
    if not playlist_name or not song_path:
        return jsonify({"success": False}), 400
    toggle_playlist_song_db(playlist_name, song_path)
    return jsonify({"success": True})


@app.route('/api/lyrics', methods=['POST'])
def get_lyrics():
    song_name = request.json.get('name', '')
    clean_name = os.path.splitext(song_name)[0]
    try:
        response = requests.get(f"https://lrclib.net/api/search?q={clean_name}", timeout=3)
        if response.status_code == 200:
            results = response.json()
            if results and len(results) > 0:
                song_data = results[0]
                lyrics = song_data.get('plainLyrics') or song_data.get('syncedLyrics')
                if lyrics:
                    return jsonify({
                        "success": True, "lyrics": lyrics,
                        "track": song_data.get('trackName'),
                        "artist": song_data.get('artistName')
                    })
        return jsonify({"success": False, "error": "Paroles introuvables."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/stream/<path:subpath>')
def stream(subpath):
    safe = _safe_path(subpath)
    if safe is None:
        return jsonify({"error": "Acces refuse"}), 403
    return send_file(safe)


# =============================================================================
# IMPORT DRAG & DROP
# =============================================================================

@app.route('/api/import', methods=['POST'])
def import_files():
    files = request.files.getlist('files')
    saved = []
    for f in files:
        if f.filename.lower().endswith(('.mp3', '.wav', '.ogg', '.flac', '.m4a')):
            filename = secure_filename(f.filename)
            dest = os.path.join(MUSIC_DIR, filename)
            # Eviter l'ecrasement
            if os.path.exists(dest):
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(MUSIC_DIR, f"{base}_{counter}{ext}")
                    counter += 1
            f.save(dest)
            saved.append(os.path.basename(dest))
    return jsonify({"success": True, "saved": saved, "count": len(saved)})


# =============================================================================
# EDITION METADONNEES
# =============================================================================

@app.route('/api/update_metadata', methods=['POST'])
def update_metadata():
    path = request.json.get('path')
    title = request.json.get('title')
    artist = request.json.get('artist')
    album = request.json.get('album')
    genre = request.json.get('genre')
    if not path:
        return jsonify({"success": False, "error": "Chemin manquant"}), 400
    safe = _safe_path(path)
    if safe is None:
        return jsonify({"success": False, "error": "Acces refuse"}), 403
    if not MUTAGEN_AVAILABLE:
        return jsonify({"success": False, "error": "mutagen non installe"}), 500
    ok = save_metadata(safe, title, artist, album, genre)
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Format non supporte pour l'ecriture"}), 400

# =============================================================================
# EXPLORATION EN LIGNE (AUDIUS — musique libre, publiee par les artistes)
# =============================================================================

ONLINE_DOWNLOAD_DIR = os.path.join(os.path.expanduser('~'), 'Musique', 'Musiques')
AUDIUS_APP_NAME = 'RESONANCE'
_audius_host = None


def get_audius_host():
    """Trouve un serveur d'API Audius operationnel (mis en cache)."""
    global _audius_host
    if _audius_host:
        return _audius_host
    try:
        r = requests.get('https://api.audius.co', timeout=6)
        for host in r.json().get('data', []):
            try:
                test = requests.get(
                    f"{host}/v1/tracks/trending",
                    params={'app_name': AUDIUS_APP_NAME, 'limit': 1},
                    timeout=6
                )
                if test.status_code == 200:
                    _audius_host = host
                    return _audius_host
            except requests.RequestException:
                continue
    except (requests.RequestException, ValueError):
        pass
    _audius_host = 'https://discoveryprovider.audius.co'
    return _audius_host


def _shape_tracks(tracks):
    results = []
    for t in tracks:
        if t.get('is_streamable') is False:
            continue
        art = t.get('artwork') or {}
        results.append({
            'id': t.get('id') or '',
            'title': t.get('title') or 'Sans titre',
            'artist': (t.get('user') or {}).get('name') or 'Artiste inconnu',
            'duration': int(t.get('duration') or 0),
            'artwork': art.get('480x480') or art.get('150x150') or art.get('1000x1000') or '',
            'genre': t.get('genre') or '',
            'downloadable': bool(t.get('downloadable')),
        })
    return results


@app.route('/api/search_online')
def search_online():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"results": []})
    try:
        host = get_audius_host()
        r = requests.get(
            f"{host}/v1/tracks/search",
            params={'query': query, 'app_name': AUDIUS_APP_NAME, 'limit': 100},
            timeout=12
        )
        r.raise_for_status()
        return jsonify({"results": _shape_tracks(r.json().get('data', []))})
    except (requests.RequestException, ValueError) as e:
        return jsonify({"results": [], "error": f"Recherche impossible : {e}"}), 502


@app.route('/api/trending_online')
def trending_online():
    try:
        host = get_audius_host()
        r = requests.get(
            f"{host}/v1/tracks/trending",
            params={'app_name': AUDIUS_APP_NAME, 'limit': 100},
            timeout=12
        )
        r.raise_for_status()
        return jsonify({"results": _shape_tracks(r.json().get('data', []))})
    except (requests.RequestException, ValueError) as e:
        return jsonify({"results": [], "error": f"Reseau indisponible : {e}"}), 502


@app.route('/api/stream_online/<track_id>')
def stream_online(track_id):
    """Proxy du flux Audius : same-origin => pas de blocage CORS,
    l'egaliseur (Web Audio) et le seek fonctionnent."""
    if not re.match(r'^[A-Za-z0-9_-]{1,64}$', track_id):
        return jsonify({"error": "ID invalide"}), 400

    req_headers = {'User-Agent': 'Mozilla/5.0'}
    range_header = request.headers.get('Range')
    if range_header:
        req_headers['Range'] = range_header

    try:
        host = get_audius_host()
        upstream = requests.get(
            f"{host}/v1/tracks/{track_id}/stream",
            params={'app_name': AUDIUS_APP_NAME},
            headers=req_headers,
            stream=True, allow_redirects=True, timeout=30
        )
    except requests.RequestException:
        return jsonify({"error": "Flux indisponible"}), 502

    if upstream.status_code not in (200, 206):
        upstream.close()
        return jsonify({"error": "Flux indisponible"}), 502

    resp_headers = {}
    for h in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    resp_headers.setdefault('Content-Type', 'audio/mp4')
    resp_headers.setdefault('Accept-Ranges', 'bytes')

    def relay():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(relay()),
        status=upstream.status_code,
        headers=resp_headers,
    )


def _unique_dest(directory, base, ext):
    dest = os.path.join(directory, base + ext)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(directory, f"{base}_{counter}{ext}")
        counter += 1
    return dest


def _tag_downloaded_file(path, title, artist, artwork_bytes=None):
    """Ecrit titre/artiste (+ pochette integree) dans le fichier telecharge."""
    if not MUTAGEN_AVAILABLE:
        return
    try:
        audio = MP3(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["TIT2"] = TIT2(encoding=3, text=title)
        audio.tags["TPE1"] = TPE1(encoding=3, text=artist)
        if artwork_bytes:
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3,
                                desc='Cover', data=artwork_bytes))
        audio.save()
    except Exception:
        try:
            from mutagen.mp4 import MP4Cover
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio["\xa9nam"] = [title]
            audio["\xa9ART"] = [artist]
            if artwork_bytes:
                audio["covr"] = [MP4Cover(artwork_bytes,
                                          imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        except Exception:
            pass


@app.route('/api/download_online', methods=['POST'])
def download_online():
    data = request.json or {}
    track_id = (data.get('id') or '').strip()
    title = (data.get('title') or 'sans titre').strip()
    artist = (data.get('artist') or 'artiste inconnu').strip()
    artwork_url = data.get('artwork') or ''

    if not re.match(r'^[A-Za-z0-9_-]{1,64}$', track_id):
        return jsonify({"success": False, "error": "ID invalide"}), 400

    os.makedirs(ONLINE_DOWNLOAD_DIR, exist_ok=True)

    try:
        host = get_audius_host()

        # Pochette (pour l'integrer dans le fichier)
        artwork_bytes = None
        if artwork_url:
            try:
                ar = requests.get(artwork_url, timeout=10)
                if ar.status_code == 200:
                    artwork_bytes = ar.content
            except requests.RequestException:
                pass

        # 1) Fichier de telechargement officiel si l'artiste l'autorise
        download_url, ext = None, '.mp3'
        try:
            meta = requests.get(
                f"{host}/v1/tracks/{track_id}",
                params={'app_name': AUDIUS_APP_NAME}, timeout=10
            ).json().get('data') or {}
            if meta.get('downloadable'):
                durls = meta.get('download_urls') or {}
                for key, candidate_ext in (('mp3', '.mp3'), ('aac', '.m4a'), ('wav', '.wav')):
                    if durls.get(key):
                        download_url, ext = durls[key], candidate_ext
                        break
        except (requests.RequestException, ValueError):
            pass

        if download_url:
            r = requests.get(download_url, stream=True, timeout=30)
        else:
            # 2) Sinon : enregistrement du flux public diffuse par Audius
            r = requests.get(
                f"{host}/v1/tracks/{track_id}/stream",
                params={'app_name': AUDIUS_APP_NAME},
                stream=True, allow_redirects=True, timeout=30
            )
            ctype = r.headers.get('Content-Type', '')
            ext = '.m4a' if ('mp4' in ctype or 'aac' in ctype) else '.mp3'
        r.raise_for_status()

        base = secure_filename(f"{artist} - {title}") or f"piste_{track_id[:8]}"
        dest = _unique_dest(ONLINE_DOWNLOAD_DIR, base, ext)
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        r.close()

        _tag_downloaded_file(dest, title, artist, artwork_bytes)

        rel_path = os.path.relpath(dest, MUSIC_DIR).replace('\\', '/')
        return jsonify({
            "success": True,
            "file": os.path.basename(dest),
            "library_path": rel_path,
            "dir": ONLINE_DOWNLOAD_DIR
        })
    except requests.RequestException as e:
        return jsonify({"success": False, "error": f"Telechargement impossible : {e}"}), 502
    except OSError as e:
        return jsonify({"success": False, "error": f"Erreur disque : {e}"}), 500

# =============================================================================
# LANCEMENT
# =============================================================================

if __name__ == '__main__':
    init_db()
    print(f"RESONANCE AUDIO SERVER ACTIF - Dossier : {MUSIC_DIR}")
    print(f"Base de donnees : {os.path.abspath(DB_FILE)}")
    if not MUTAGEN_AVAILABLE:
        print("\nNOTE: Installez 'mutagen' pour activer les metadonnees:")
        print("  pip install mutagen\n")
    app.run(debug=True, port=5000)