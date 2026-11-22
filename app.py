import re
import os
import time
import shutil
import tempfile
import threading
import sqlite3
import requests
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

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    print("=" * 60)
    print("WARNING: yt-dlp n'est pas installe (recherche YouTube inactive).")
    print("Installez-le avec:  pip install yt-dlp")
    print("=" * 60)

app = Flask(__name__)

MUSIC_DIR = os.environ.get('MUSIC_DIR') or os.path.join(os.path.expanduser('~'), 'Musique')
os.makedirs(MUSIC_DIR, exist_ok=True)   # cree le dossier musical s'il n'existe pas
DB_FILE = os.environ.get('DB_FILE', 'resonance.db')
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lyrics_cache (
            cache_key TEXT PRIMARY KEY,
            synced TEXT,
            plain TEXT,
            track TEXT,
            artist TEXT,
            fetched_at INTEGER
        )
    """)
    # Cache lyrics : les resultats negatifs ne sont gardes que 24h
    cursor.execute("DELETE FROM lyrics_cache WHERE fetched_at < ?", (int(time.time()) - 86400,))
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


# =============================================================================
# PAROLES (LRCLIB : base libre, sans cle API)
# =============================================================================

LRCLIB_HEADERS = {'User-Agent': 'ResonanceAudioServer/2.0 (personal music server)'}
_lyrics_pending = set()   # evite le spam de requetes identiques concurrentes


def _lyr_cache_get(key):
    try:
        with get_db() as conn:
            row = conn.execute(
                'SELECT synced, plain, track, artist, fetched_at FROM lyrics_cache WHERE cache_key = ?',
                (key,)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def _lyr_cache_put(key, synced, plain, track, artist):
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO lyrics_cache (cache_key, synced, plain, track, artist, fetched_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (key, synced or '', plain or '', track or '', artist or '',
                 int(time.time())))
    except sqlite3.Error:
        pass


def _clean_title(title, artist):
    """Ramene un titre de fichier/YouTube a un titre cherchable.
    Enleve (Official Video), [HD], "feat",Numero de video, etc."""
    t = (title or '').strip()
    if not t:
        return ''
    t = re.sub(r'\([^)]*\b(video|clip|official|audio|lyrics?|hd|4k|mv)\b[^)]*\)', ' ', t, flags=re.I)
    t = re.sub(r'\[[^\]]*\b(video|clip|official|audio|lyrics?|hd|4k|mv)\b[^\]]*\]', ' ', t, flags=re.I)
    t = re.sub(r'\b(official|video|clip|audio|music\s?video|lyrics?)\b', ' ', t, flags=re.I)
    t = re.sub(r'\bfeat\.?\s+[^\-|]+', ' ', t, flags=re.I)
    t = re.sub(r'\bft\.?\s+[^\-|]+', ' ', t, flags=re.I)
    # "A - B (feat. C) ..." : garde A et B
    t = re.sub(r'\s*[-–]\s*(official|video|clip|audio)\b.*$', '', t, flags=re.I)
    t = re.sub(r'[\|\•]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' -–.')
    return t


def _lrclib_fetch(title, artist, duration):
    """Interroge LRCLib : get precis (titre+artiste+duree) puis search en secours.
    Retourne (synced, plain, track, artist) ou None."""
    import urllib.parse
    base = 'https://lrclib.net/api/'
    try:
        if duration and duration > 0:
            r = requests.get(f'{base}get',
                             params={'track_name': title, 'artist_name': artist, 'duration': duration},
                             headers=LRCLIB_HEADERS, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get('plainLyrics') or d.get('syncedLyrics'):
                    return d.get('syncedLyrics') or '', d.get('plainLyrics') or '', \
                           d.get('trackName') or title, d.get('artistName') or artist
        q = f'{artist} {title}'.strip() if artist else title
        r = requests.get(f'{base}search', params={'q': q},
                         headers=LRCLIB_HEADERS, timeout=6)
        if r.status_code == 200:
            for d in r.json():
                if d.get('syncedLyrics') or d.get('plainLyrics'):
                    return d.get('syncedLyrics') or '', d.get('plainLyrics') or '', \
                           d.get('trackName') or title, d.get('artistName') or artist
    except requests.RequestException:
        pass
    return None


@app.route('/api/lyrics', methods=['POST'])
def get_lyrics():
    # Paroles depuis LRCLib (titre + artiste + duree quand disponibles) avec cache SQLite.
    # Reponse : {"success": true, "synced": "...", "plain": "...", "source": "lrclib"}
    data = request.json or {}
    title = (data.get('title') or data.get('name') or '').strip()
    artist = (data.get('artist') or '').strip()
    duration = int_or_zero(data.get('duration'))
    song_path = (data.get('path') or '').strip()

    # Titre nettoyé : les noms de fichiers et titres YouTube sont souvent charges
    title = _clean_title(title, artist)
    if not title:
        return jsonify({"success": False, "error": "Titre manquant"}), 400

    cache_key = f'{artist.lower()}|{title.lower()}'
    cached = _lyr_cache_get(cache_key)
    if cached and (cached.get('plain') or cached.get('synced')):
        return jsonify({"success": True, "synced": cached['synced'],
                        "plain": cached['plain'], "track": cached['track'],
                        "artist": cached['artist'], "source": "cache"})

    if cache_key in _lyrics_pending:
        return jsonify({"success": False, "error": "Recherche en cours, reessayez dans un instant."}), 429
    _lyrics_pending.add(cache_key)
    try:
        found = _lrclib_fetch(title, artist, duration)
        if not found and artist:
            # 2e essai sans l'artiste (tags vides ou erronees derri
            found = _lrclib_fetch(title, '', duration)
        if not found and song_path:
            # 3e essai : titre deduit du nom de fichier
            base = os.path.splitext(os.path.basename(song_path))[0]
            if ' - ' in base:
                file_artist, file_title = base.split(' - ', 1)
                found = _lrclib_fetch(_clean_title(file_title, file_artist), file_artist.strip(), duration)
        if not found:
            _lyr_cache_put(cache_key, '', '', '', '')   # negatif 24h -> evite de re-chercher en boucle
            return jsonify({"success": False, "error": "Paroles introuvables pour ce titre."})
        synced, plain, track, lyr_artist = found
        _lyr_cache_put(cache_key, synced, plain, track, lyr_artist)
        return jsonify({"success": True, "synced": synced, "plain": plain,
                        "track": track, "artist": lyr_artist, "source": "lrclib"})
    finally:
        _lyrics_pending.discard(cache_key)


def int_or_zero(v):
    try:
        n = int(v)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


# =============================================================================
# CLIP VIDEO (relais du flux YouTube, comme l'audio)
# =============================================================================

_yt_video_cache = {}    # track_id -> (video_id, expire_ts)


def _yt_lookup_video(query):
    # Cherche le clip officiel et retourne l'ID de la video, ou None.
    opts = _yt_base_opts()
    opts['extract_flat'] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f'ytsearch5:{query}', download=False)
        for e in (info.get('entries') or []):
            vid = e.get('id') or ''
            duration = e.get('duration') or 0
            if not vid or e.get('is_live'):
                continue
            # Cote clip : ~2 a 7 min ; les films/sets/mix depassent 15 min
            if duration and duration > 900:
                continue
            return vid, duration
    except Exception:
        pass
    return None, 0


@app.route('/api/clip', methods=['POST'])
def get_clip():
    # Retourne l'ID du clip YouTube pour le morceau en cours.
    # Ordre : id YouTube connu (piste jouee depuis YouTube) > recherche
    # "artiste titre clip officiel" > recherche simple. Fallback UI : pochette.
    if not YTDLP_AVAILABLE:
        return jsonify({"success": False, "error": "yt-dlp non installe"}), 500
    data = request.json or {}
    known_id = (data.get('yt_id') or '').strip()
    if known_id and re.match(r'^[A-Za-z0-9_-]{6,20}$', known_id):
        return jsonify({"success": True, "video_id": known_id, "matched": "known"})
    title = (data.get('title') or '').strip()
    artist = (data.get('artist') or '').strip()
    if not title:
        return jsonify({"success": False, "error": "Titre manquant"}), 400

    clean = _clean_title(title, artist)
    key = f'{artist.lower()}|{clean.lower()}'
    with _yt_lock:
        cached = _yt_video_cache.get(key)
        if cached and time.time() < cached[1]:
            return jsonify({"success": True, "video_id": cached[0], "matched": "search"})

    for query in (f'{artist} {clean} clip officiel'.strip(), f'{artist} {clean}'.strip()):
        if not query.strip():
            continue
        vid, _dur = _yt_lookup_video(query)
        if vid:
            with _yt_lock:
                _yt_video_cache[key] = (vid, time.time() + 6 * 3600)
            return jsonify({"success": True, "video_id": vid, "matched": "search"})
    return jsonify({"success": False, "error": "Aucun clip trouve : la pochette est affichee."})


def _yt_resolve_video(video_id):
    # URL du flux video (avec piste audio integree), cache 30 min.
    # Formats PROGRESSIFS uniquement (video+audio dans un seul flux mp4) :
    # les formats separes ne sont pas jouables par la balise <video> du
    # navigateur sans mux ffmpeg cote serveur.
    with _yt_lock:
        cached = _yt_video_cache.get('url:' + video_id)
        if cached and time.time() < cached[1]:
            return cached[0]
    opts = _yt_base_opts()
    opts['format'] = ('best[protocol^=http][acodec!=none][vcodec!=none]/'
                      'best[protocol^=https][acodec!=none][vcodec!=none]/'
                      'b[acodec!=none][vcodec!=none]/best')
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
        url = info.get('url')
        if not url:
            raise yt_dlp.utils.DownloadError('Flux video introuvable.')
        with _yt_lock:
            _yt_video_cache['url:' + video_id] = (url, time.time() + YT_CACHE_TTL)
        return url
    except yt_dlp.utils.DownloadError as e:
        raise _YtError(_yt_error_message(e))


@app.route('/api/stream_video/<video_id>')
def stream_video(video_id):
    # Proxy du flux VIDEO YouTube (avec son) : same-origin, seek via Range.
    if not re.match(r'^[A-Za-z0-9_-]{6,20}$', video_id):
        return jsonify({"error": "ID invalide"}), 400
    if not YTDLP_AVAILABLE:
        return jsonify({"error": "yt-dlp non installe"}), 500
    try:
        url = _yt_resolve_video(video_id)
    except _YtError as e:
        return jsonify({"error": str(e)}), e.code

    req_headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.youtube.com/'}
    range_header = request.headers.get('Range')
    if range_header:
        req_headers['Range'] = range_header

    upstream = None
    for attempt in (0, 1):
        try:
            upstream = requests.get(url, headers=req_headers, stream=True, timeout=30)
        except requests.RequestException:
            return jsonify({"error": "Flux indisponible"}), 502
        if upstream.status_code in (200, 206):
            break
        upstream.close()
        if attempt == 0:
            with _yt_lock:
                _yt_video_cache.pop('url:' + video_id, None)
            try:
                url = _yt_resolve_video(video_id)
            except _YtError as e:
                return jsonify({"error": str(e)}), e.code
        else:
            return jsonify({"error": "Flux indisponible"}), 502

    resp_headers = {}
    for h in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    resp_headers.setdefault('Content-Type', 'video/mp4')

    def relay():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(stream_with_context(relay()), status=upstream.status_code, headers=resp_headers)
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
    # Balayage du dossier musical : un fichier illisible (telechargement en
    # cours, tags corrompus...) ne doit jamais casser la bibliotheque entiere.
    music_files = []
    for root, dirs, files in os.walk(directory, onerror=lambda e: None):
        for file in files:
            if not file.lower().endswith(('.mp3', '.wav', '.ogg', '.flac', '.m4a')):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, directory).replace('\\', '/')
            try:
                mtime = os.path.getmtime(full_path)
                meta = extract_metadata(full_path)
            except Exception:
                continue   # fichier momentanement illisible : ignore, sans faire planter l'etat
            if not meta["duration"]:
                # Pas de duree lisible : binaire corrompu ou telechargement en
                # cours — pas une musique jouable, on ne l'affiche pas.
                continue
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
const CACHE_NAME = 'resonance-v8';
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
        "playlists": get_playlists(),
        "music_dir": MUSIC_DIR
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

# Les telechargements arrivent directement dans le dossier musical de l'utilisateur
ONLINE_DOWNLOAD_DIR = MUSIC_DIR
AUDIUS_APP_NAME = 'RESONANCE'
_audius_host = None


# =============================================================================
# ETAT DE LA CONNEXION INTERNET
# =============================================================================

_net_lock = threading.Lock()
_net_cache = {"ts": 0.0, "online": False, "audius_ok": False, "youtube_ok": False}


def _check_internet():
    # Verifie la connectivite internet (resultat en cache 10 s).
    global _audius_host
    with _net_lock:
        now = time.time()
        if now - _net_cache["ts"] < 10:
            return dict(_net_cache)

        # Internet global : hote neutre
        online = False
        try:
            r = requests.get('https://www.gstatic.com/generate_204', timeout=3)
            online = (r.status_code == 204)
        except requests.RequestException:
            pass

        audius_ok = False
        try:
            r = requests.get('https://api.audius.co', timeout=3)
            audius_ok = (r.status_code == 200)
        except requests.RequestException:
            pass
        if not audius_ok:
            _audius_host = None   # forcera une re-resolution au prochain appel

        youtube_ok = False
        try:
            r = requests.head('https://www.youtube.com/generate_204', timeout=3, allow_redirects=True)
            youtube_ok = (r.status_code == 204)
        except requests.RequestException:
            try:
                r = requests.head('https://www.youtube.com', timeout=3, allow_redirects=True)
                youtube_ok = (r.status_code == 200)
            except requests.RequestException:
                pass

        _net_cache["ts"] = now
        _net_cache["online"] = online
        _net_cache["audius_ok"] = audius_ok
        _net_cache["youtube_ok"] = youtube_ok
        return dict(_net_cache)


@app.route('/api/connection_status')
def connection_status():
    state = _check_internet()
    return jsonify({
        "online": state["online"],
        "audius_ok": state["audius_ok"],
        "youtube_ok": state["youtube_ok"]
    })


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
                if not download_url:
                    # Endpoint officiel de telechargement d'Audius
                    download_url, ext = f"{host}/v1/tracks/{track_id}/download", '.mp3'
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

        # Affine l'extension d'apres le type MIME reel du fichier recu
        ctype = (r.headers.get('Content-Type') or '').lower()
        if 'wav' in ctype:
            ext = '.wav'
        elif 'flac' in ctype:
            ext = '.flac'
        elif 'ogg' in ctype:
            ext = '.ogg'
        elif 'mp4' in ctype or 'aac' in ctype or 'm4a' in ctype:
            ext = '.m4a'
        else:
            ext = '.mp3'

        # Nom de fichier lisible : accents conserves, caracteres interdits retires
        safe_base = re.sub(r'[\\/:*?"<>|]+', ' ', f"{artist} - {title}")
        safe_base = re.sub(r'\s+', ' ', safe_base).strip(' .')[:150]
        base = safe_base or f"piste_{track_id[:8]}"
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
# EXPLORATION YOUTUBE (catalogue complet via yt-dlp)
# =============================================================================

YTDLP_COOKIES = os.environ.get('YTDLP_COOKIES_FILE', '')
# Deblocage YouTube (PO token) : deux modes possibles.
# 1) Script integre (defaut, un seul conteneur) : le dossier du serveur de
#    deblocage est installe a cote de app.py par le Dockerfile (/pot-server).
# 2) Serveur HTTP optionnel : si POT_BASE_URL est defini (ex. ancien conteneur
#    "pot" sur un hote Docker qui l'accepte), il est utilise en priorite.
POT_BASE_URL = os.environ.get('POT_BASE_URL', '')
POT_SERVER_HOME = (os.environ.get('YT_POT_SERVER_HOME')
                   or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pot-server'))
YT_CACHE_TTL = 1800   # 30 min

_yt_lock = threading.Lock()
_yt_url_cache = {}      # video_id -> (url, expire_ts)
_yt_search_cache = {}   # requete -> (ts, resultats)

# Message simple, actionnable, sans jargon technique.
_BOT_MSG = ("YouTube a bloque votre serveur pour le moment.\n"
            "Solution : ouvrez le fichier docker-compose.yml et suivez les 3 etapes "
            "commentees (section cookies) ; apres redemarrage tout fonctionne.\n"
            "Etapes : 1) connectez-vous a YouTube dans votre navigateur 2) installez "
            "l'extension \"Get cookies.txt LOCALLY\" 3) exportez le fichier cookies.txt "
            "a cote du docker-compose.yml et decommentez les 2 lignes indiquees.")

# Message pour l'erreur 403 typique du telechargement (PO token manquant).
_POT_MSG = ("Le telechargement a ete refuse par YouTube (403).\n"
            "L'application integre un deblocage automatique : relancez "
            "\"docker compose up -d --build\" pour en beneficier. Si le refus "
            "persiste, suivez la section cookies du docker-compose.yml (3 etapes).")


class _YtError(Exception):
    def __init__(self, message, code=502):
        super().__init__(message)
        self.code = code


def _pot_available():
    # Le service de deblocage HTTP optionnel tourne-il ? (mode script = True)
    if not POT_BASE_URL:
        return os.path.isfile(os.path.join(POT_SERVER_HOME, 'build', 'generate_once.js'))
    try:
        import requests as _r
        r = _r.get(f'{POT_BASE_URL}/ping', timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _yt_base_opts():
    # Options communes yt-dlp ; moteur JS (node/deno) si present, cookies si fournis,
    # deblocage PO token : script integre (+ serveur HTTP optionnel si defini).
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 15,
    }
    runtimes = [r for r in ('node', 'deno') if shutil.which(r)]
    if runtimes:
        opts['js_runtimes'] = {r: {} for r in runtimes}
    if YTDLP_COOKIES and os.path.exists(YTDLP_COOKIES):
        opts['cookiefile'] = YTDLP_COOKIES
    if YTDLP_AVAILABLE:
        try:
            ea = {}
            if POT_BASE_URL:
                ea['youtubepot-bgutilhttp'] = {'base_url': [POT_BASE_URL]}
            if os.path.isfile(os.path.join(POT_SERVER_HOME, 'build', 'generate_once.js')):
                ea['youtubepot-bgutilscript'] = {'server_home': [POT_SERVER_HOME]}
            if ea:
                opts['extractor_args'] = ea
        except Exception:
            pass
    return opts


def _yt_error_message(exc):
    # Traduit les erreurs techniques yt-dlp en phrases simples et actionnables.
    msg = str(exc)
    if 'Sign in to confirm' in msg or 'not a bot' in msg:
        return _BOT_MSG
    if 'HTTP Error 403' in msg or 'unable to download video data' in msg:
        return _POT_MSG if not _pot_available() else (
            "YouTube refuse ce telechargement (403) malgre le service de deblocage.\n"
            "Reessayez : le refus est souvent temporaire. Si cela persiste, suivez la "
            "section cookies du docker-compose.yml.")
    if 'HTTP Error 429' in msg:
        return ("Vous avez fait trop de demandes d'un coup (429).\n"
                "Patientez 1 a 2 minutes puis reessayez.")
    if 'Video unavailable' in msg or 'video unavailable' in msg or 'Private video' in msg:
        return ("Cette video n'est plus disponible sur YouTube "
                "(supprimee ou privee). Essayez une autre piste.")
    if 'malformed' in msg.lower() or 'not a valid URL' in msg:
        return "L'adresse de cette video est incorrecte."
    if 'Connection' in msg or 'timed out' in msg or 'timeout' in msg.lower():
        return ("Connexion a YouTube impossible pour le moment.\n"
                "Verifiez que votre serveur a acces a internet, puis reessayez.")
    return ("Le telechargement a echoue.\n"
            "Reessayez dans quelques minutes ; si cela persiste, relancez "
            "\"docker compose up -d --build\".")


def _yt_resolve_stream(track_id):
    # Retourne l'URL du flux audio (cache 30 min, re-extrait si expiree).
    with _yt_lock:
        cached = _yt_url_cache.get(track_id)
        if cached and time.time() < cached[1]:
            return cached[0]
    opts = _yt_base_opts()
    opts['format'] = 'bestaudio[ext=m4a]/bestaudio'
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f'https://www.youtube.com/watch?v={track_id}', download=False)
        url = info.get('url')
        if not url:
            raise yt_dlp.utils.DownloadError('Flux audio introuvable.')
        with _yt_lock:
            _yt_url_cache[track_id] = (url, time.time() + YT_CACHE_TTL)
        return url
    except yt_dlp.utils.DownloadError as e:
        raise _YtError(_yt_error_message(e))


@app.route('/api/search_youtube')
def search_youtube():
    # Recherche plate : rapide, ne resolve pas chaque video individuellement.
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"results": []})
    if not YTDLP_AVAILABLE:
        return jsonify({"results": [], "error": "yt-dlp non installe : pip install yt-dlp"}), 500
    key = query.lower()
    with _yt_lock:
        cached = _yt_search_cache.get(key)
        if cached and time.time() - cached[0] < YT_CACHE_TTL:
            return jsonify({"results": cached[1]})
    opts = _yt_base_opts()
    opts['extract_flat'] = True
    try:
        # Prefixe ytsearch explicite : default_search est ignore par certaines
        # versions de yt-dlp. Une URL collee dans la recherche est resolue
        # directement comme une piste unique.
        target = query if re.match(r'^https?://', query) else f'ytsearch{25}:{query}'
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
        entries = info.get('entries') or []
        if info.get('_type') != 'playlist' and info.get('id'):
            entries = [info]
        results, seen = [], set()
        for e in entries:
            vid = e.get('id') or ''
            duration = e.get('duration')
            if not vid or vid in seen or e.get('is_live') or not duration:
                continue
            seen.add(vid)
            results.append({
                'id': vid,
                'title': e.get('title') or 'Sans titre',
                'artist': e.get('channel') or e.get('uploader') or 'YouTube',
                'duration': int(duration),
                'artwork': f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg',
                'views': e.get('view_count'),
                'source': 'youtube',
            })
        with _yt_lock:
            _yt_search_cache[key] = (time.time(), results)
        return jsonify({"results": results})
    except yt_dlp.utils.DownloadError as e:
        return jsonify({"results": [], "error": _yt_error_message(e)}), 502
    except Exception as e:
        return jsonify({"results": [], "error": f"YouTube : {str(e)[:180]}"}), 502


@app.route('/api/stream_youtube/<track_id>')
def stream_youtube(track_id):
    # Proxy du flux audio YouTube : same-origin => EQ (Web Audio) et seek fonctionnent.
    if not re.match(r'^[A-Za-z0-9_-]{6,20}$', track_id):
        return jsonify({"error": "ID invalide"}), 400
    if not YTDLP_AVAILABLE:
        return jsonify({"error": "yt-dlp non installe"}), 500

    try:
        url = _yt_resolve_stream(track_id)
    except _YtError as e:
        return jsonify({"error": str(e)}), e.code

    req_headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.youtube.com/'}
    range_header = request.headers.get('Range')
    if range_header:
        req_headers['Range'] = range_header

    upstream = None
    for attempt in (0, 1):
        try:
            upstream = requests.get(url, headers=req_headers, stream=True, timeout=30)
        except requests.RequestException:
            return jsonify({"error": "Flux indisponible"}), 502
        if upstream.status_code in (200, 206):
            break
        upstream.close()
        if attempt == 0:
            # URL expiree ou refusee (403/410) : re-extraction complete
            with _yt_lock:
                _yt_url_cache.pop(track_id, None)
            try:
                url = _yt_resolve_stream(track_id)
            except _YtError as e:
                return jsonify({"error": str(e)}), e.code
        else:
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

    return Response(stream_with_context(relay()), status=upstream.status_code, headers=resp_headers)


@app.route('/api/download_youtube', methods=['POST'])
def download_youtube():
    # Telecharge la piste audio dans MUSIC_DIR, tags + pochette integres.
    if not YTDLP_AVAILABLE:
        return jsonify({"success": False, "error": "yt-dlp non installe : pip install yt-dlp"}), 500
    data = request.json or {}
    track_id = (data.get('id') or '').strip()
    title = (data.get('title') or 'sans titre').strip()
    artist = (data.get('artist') or 'YouTube').strip()
    artwork_url = data.get('artwork') or f'https://i.ytimg.com/vi/{track_id}/hqdefault.jpg'
    if not re.match(r'^[A-Za-z0-9_-]{6,20}$', track_id):
        return jsonify({"success": False, "error": "ID invalide"}), 400

    ffmpeg = shutil.which('ffmpeg')
    os.makedirs(ONLINE_DOWNLOAD_DIR, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='resonance_yt_')

    opts = _yt_base_opts()
    opts.update({
        'outtmpl': os.path.join(workdir, '%(id)s.%(ext)s'),
        'retries': 3,
        'format': ('bestaudio/best' if ffmpeg else
                   'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=ogg]/bestaudio'),
    })
    if ffmpeg:
        opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
            {'key': 'FFmpegMetadata'},
        ]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(f'https://www.youtube.com/watch?v={track_id}', download=True)

        produced, allowed = None, ('.mp3', '.m4a', '.ogg', '.wav', '.flac')
        for name in sorted(os.listdir(workdir)):
            if name.startswith(track_id) and name.lower().endswith(allowed):
                produced = os.path.join(workdir, name)
                break
        if not produced:
            raise _YtError("Format audio non exploitable : installez ffmpeg puis reessayez.", 500)

        safe_base = re.sub(r'[\\/:*?"<>|]+', ' ', f"{artist} - {title}")
        safe_base = re.sub(r'\s+', ' ', safe_base).strip(' .')[:150] or f"youtube_{track_id[:8]}"
        dest = _unique_dest(ONLINE_DOWNLOAD_DIR, safe_base, os.path.splitext(produced)[1].lower())
        shutil.move(produced, dest)

        artwork_bytes = None
        if artwork_url:
            try:
                ar = requests.get(artwork_url, timeout=10)
                if ar.status_code == 200:
                    artwork_bytes = ar.content
            except requests.RequestException:
                pass
        _tag_downloaded_file(dest, title, artist, artwork_bytes)

        rel_path = os.path.relpath(dest, MUSIC_DIR).replace('\\', '/')
        return jsonify({"success": True, "file": os.path.basename(dest),
                        "library_path": rel_path, "dir": ONLINE_DOWNLOAD_DIR})
    except yt_dlp.utils.DownloadError as e:
        code = 502   # 401 laisserait croire a un probleme de connexion a l'app
        return jsonify({"success": False, "error": _yt_error_message(e)}), code
    except _YtError as e:
        return jsonify({"success": False, "error": str(e)}), e.code
    except OSError as e:
        return jsonify({"success": False, "error": f"Erreur disque : {e}"}), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

# =============================================================================
# LANCEMENT
# =============================================================================

init_db()   # idempotent : necessaire aussi sous gunicorn/docker (import du module)

if __name__ == '__main__':
    print(f"RESONANCE AUDIO SERVER ACTIF - Dossier : {MUSIC_DIR}")
    print(f"Base de donnees : {os.path.abspath(DB_FILE)}")
    if not MUTAGEN_AVAILABLE:
        print("\nNOTE: Installez 'mutagen' pour activer les metadonnees:")
        print("  pip install mutagen\n")
    if not YTDLP_AVAILABLE:
        print("NOTE: Installez 'yt-dlp' pour la recherche YouTube:")
        print("  pip install yt-dlp\n")
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5000'))
    app.run(debug=os.environ.get('FLASK_DEBUG', '1') == '1', host=host, port=port)
