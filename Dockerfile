# RESONANCE — serveur audio personnel
FROM python:3.12-slim

# ffmpeg : conversion MP3 + lecture des formats ;
# nodejs : moteur JS requis par yt-dlp pour extraire les flux YouTube recents
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

# MUSIC_DIR : votre bibliotheque (volume) — DB_FILE : base SQLite (volume)
# YTDLP_COOKIES_FILE : cookies YouTube si l'IP du serveur est verifiee par Google
# POT_BASE_URL : service de deblocage YouTube (conteneur "pot" du docker-compose)
ENV MUSIC_DIR=/music \
    DB_FILE=/data/resonance.db \
    HOST=0.0.0.0 \
    PORT=5000 \
    FLASK_DEBUG=0 \
    POT_BASE_URL=http://pot:4416

VOLUME ["/music", "/data"]
EXPOSE 5000

# gunicorn : 2 processus x 8 threads, timeout large pour les telechargements
CMD ["sh", "-c", "exec gunicorn --workers 2 --threads 8 --timeout 300 --bind ${HOST}:${PORT} app:app"]
