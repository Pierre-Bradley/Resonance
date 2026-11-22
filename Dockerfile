# RESONANCE — serveur audio personnel (UN SEUL conteneur, tout est integre)

# Etape 1 : image officielle du deblocage YouTube (PO token). On copie ses
# fichiers ci-dessous au lieu de demarrer un 2e conteneur : certains hotes
# Docker restreints (NAS, LXC, VM durcie) refusent de creer ce 2e conteneur
# (erreur "OCI runtime create failed ... sysctl ... permission denied").
# La copie de fichiers, elle, fonctionne partout.
FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2-node AS pot

# Etape 2 : l'application
FROM python:3.12-slim

# ffmpeg : conversion MP3 + lecture des formats ;
# libstdc++6 : requise par le moteur node copie ci-dessous
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# node (moteur JS requis par yt-dlp et le deblocage) : binaire officiel repris
# de l'image ci-dessus (version 26, compatible avec les fichiers copies)
COPY --from=pot /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Service de deblocage YouTube : moteur + dependances prets a l'emploi.
# L'application le lance automatiquement a la demande (aucune action requise).
COPY --from=pot /app /pot-server

# MUSIC_DIR : votre bibliotheque (volume) — DB_FILE : base SQLite (volume)
# YTDLP_COOKIES_FILE : cookies YouTube si votre IP est bloquee par Google
# YT_POT_SERVER_HOME : dossier du deblocage integre (ne pas modifier)
ENV MUSIC_DIR=/music \
    DB_FILE=/data/resonance.db \
    HOST=0.0.0.0 \
    PORT=5000 \
    FLASK_DEBUG=0 \
    YT_POT_SERVER_HOME=/pot-server

VOLUME ["/music", "/data"]
EXPOSE 5000

# gunicorn : 2 processus x 8 threads, timeout large pour les telechargements
CMD ["sh", "-c", "exec gunicorn --workers 2 --threads 8 --timeout 300 --bind ${HOST}:${PORT} app:app"]
