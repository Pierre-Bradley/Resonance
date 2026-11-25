# RESONANCE — votre serveur de musique personnel

## Demarrer sur votre serveur (Docker)

1. Copiez tout ce dossier sur votre serveur.
2. Si une ancienne version tournait deja : `docker compose down --remove-orphans`
3. Lancez : `docker compose up -d --build`
4. Ouvrez : **http://IP-de-votre-serveur:7000** (le port 5000 etant deja pris sur votre machine)

Un seul conteneur demarre, avec le deblocage YouTube deja inclus : il genere
automatiquement les autorisations que YouTube exige des serveurs. Aucune autre
action n'est necessaire.

## Les 2 boutons du haut (vue "Explorer en ligne")

- **YouTube** : tout le catalogue — recherchez, ecoutez, telechargez.
- **Audius** : musique libre de droits, toujours accessible.

Les messages d'erreur s'affichent en bas a droite et expliquent toujours :
ce qui s'est passe, pourquoi, et quoi faire.

## Paroles, clip et bibliotheque (nouveau)

- **Paroles** : bouton microphone dans la barre du lecteur. Les paroles sont
  recherchees sur internet (base libre LRCLib, sans compte ni cle) et
  affichees en karaoke synchronise quand elles sont disponibles : la ligne en
  cours est surlignee et le texte **defile tout seul avec la musique** (la
  ligne active reste au tiers superieur du panneau) — si vous faites defiler
  vous-meme, le suivi se suspend quelques secondes puis reprend. Cliquez une
  ligne pour y sauter. Le bouton CC desactive/reactive le suivi automatique.
  Chaque recherche est mise en cache dans la base : y revenir est instantane
  et ne relance pas internet.
- **Clip** : bouton film dans la barre du lecteur. Le clip officiel est joue
  en video, relaye par votre serveur. S'il n'existe pas de clip, la pochette
  de l'album (ou de la chanson) s'affiche a la place.
- **Bibliotheque** : le bouton fleche circulaire rescane votre dossier (les
  musiques ajoutees ou telechargees apparaissent aussitot) ; le bouton
  dossier affiche le chemin reellement surveille par le serveur et le nombre
  de titres. Apres chaque telechargement, si le fichier n'apparait pas dans
  la bibliotheque, l'application vous le dit avec le dossier en cause.
- **Performance** : les tags de chaque fichier ne sont plus relus a chaque
  sondage (l'interface interroge le serveur toutes les 5 s) : un cache
  memoire par fichier ne re-analyse que les fichiers nouveaux ou modifies,
  et l'etat repond 304 sans transfert quand rien n'a change — meme sur une
  grosse bibliotheque, le sondage reste leger.
- **Suppression** : le bouton corbeille de chaque ligne efface le titre —
  le fichier sur le disque du serveur, ses favoris et playlists, et sa copie
  eventuelle du stockage hors ligne du navigateur (confirmation demandee).
- **Stockage hors ligne automatique** : tout titre telecharge par
  l'application est mis en cache du navigateur des son apparition dans la
  bibliotheque — jouable simplement et a tout moment, meme quand le serveur
  est injoignable (les telechargements anterieurs sont retrouves via le
  journal des taches persistant). Le cache reste plafonne (512 Mo par
  defaut) et evicte du plus lourd quand il depasse. Le bouton base de
  donnees de l'en-tete affiche la place reellement occupee.
- **Robustesse (3.5)** : retélécharger un titre deja present propose de le
  remplacer ou d'ignorer au lieu de creer un doublon ; la recherche ne
  filtre qu'apres 200 ms sans frappe (fluidite sur les grandes listes) ;
  la base SQLite passe en mode WAL (moins de verrous entre les processus
  du serveur ; desactive automatiquement si le systeme de fichiers le
  refuse, pour ne jamais bloquer le demarrage) ; nouveaux raccourcis —
  **L** paroles, **C** clip — et un
  aide-memoire affichable avec **?** (ou le bouton clavier de l'en-tete).
- **Serveur sans Internet : la musique locale reste instantanee** — la sonde
  de connectivite tourne en tache de fond et l'etat dernier connu repond en
  quelques millisecondes (avant, elle bloquait les 2 workers du serveur sur
  le DNS pendant des dizaines de secondes : un titre mettait plus d'une
  minute a demarrer). Les fonctions en ligne (recherche, tendances, clips,
  paroles hors cache, telechargements) echouent aussitot avec un message
  clair au lieu de pendre. Surtout, la perte d'Internet du serveur ne coupe
  JAMAIS la lecture de la bibliotheque locale ni l'enchainement de la file.
  La variable d'environnement RESONANCE_PROBE_INTERNET permet de sonner une
  passerelle personnelle (deploiement air-gap).

## Telechargements en arriere-plan et lecture resilient (nouveau)

- **Telechargements sans blocage** : cliquer un telechargement (Audius ou
  YouTube) rend la main immediatement — le travail se poursuit sur le
  serveur, meme si vous fermez l'onglet ou changez de vue. Le bouton nuage
  de l'en-tete ouvre le panneau des telechargements : chaque tache y montre
  son pourcentage, sa vitesse et le temps restant ; un compteur sur le
  bouton signale les taches en cours, et un message previent a la fin (la
  bibliotheque est rescannee toute seule). Recharger la page en plein
  telechargement n'efface rien : la tache en cours est re-adoptee a
  l'ouverture. L'etat des taches est garde dans un fichier JSON a cote de
  la base, partage entre les processus du serveur.
- **Lecture qui ne casse plus** : si un flux s'interrompt (reseau mobile,
  adresse de flux expiree), la piste est rechargee automatiquement a la
  position ou vous en etiez, avec un message clair. Si le flux reste
  injoignable, la lecture passe au titre suivant de la file — jusqu'a 3
  fois — puis s'arrete proprement en vous expliquant quoi faire. Hors
  ligne, la lecture s'arrete sans balayer inutilement toute la file.

## IMPORTANT — dossier de musique : chemin absolu dans le compose

Le docker-compose.yml contient une ligne du genre `- /root/Musique:/music`.
Mettez-y le chemin REEL de votre dossier, en absolu, entre guillemets :

    - "/home/votre-user/Musique:/music"

Deux pieges frequent qui rendent la musique "invisible" dans l'application :

- `~` ou `$HOME` : avec `sudo docker compose up`, `$HOME` vaut `/root`, donc
  le conteneur surveille `/root/Musique` — pas votre dossier. Verifiez avec
  le bouton dossier de l'application quel chemin est reellement surveille.
- Si vous avez lance la version "reseau hote" (docker-compose-alt.yml),
  c'est ce fichier-la qu'il faut corriger, pas le docker-compose.yml.

Si un telechargement reste malgre tout invisible : cliquez le bouton dossier,
comparez avec `docker exec resonance ls /music` (liste ce que le conteneur
voit reellement), et corrigez le volume dans le compose puis relancez
`docker compose up -d --build`.

## Si Docker refuse de demarrer le conteneur (erreur "OCI runtime ... sysctl ... permission denied")

Ce n'est pas l'application : c'est un bug connu de runc (CVE-2025-52881) qui
touche Docker installe dans un LXC Proxmox avec certaines versions de
containerd.io. Corriger l'hote, dans l'ordre :

1. Mettre a jour Docker (les versions recentes contiennent la correction) :
   ```
   sudo apt update
   sudo apt install --only-upgrade docker-ce docker-ce-cli containerd.io docker-compose-plugin
   docker run --rm hello-world
   ```
2. Si "hello-world" echoue encore, revenir a la derniere version saine de
   containerd.io (verifiez la liste exacte avec `apt policy containerd.io`,
   le suffixe depend de votre distribution) :
   ```
   sudo apt install -y --allow-downgrades containerd.io=1.7.28-1~debian.13~trixie
   sudo apt-mark hold containerd.io
   ```
3. Autre voie (cote Proxmox) : passer l'hote en Proxmox 9.1 ou superieur, ou
   faire tourner Docker dans une VM plutot qu'un LXC.

Si vous ne pouvez pas toucher a l'hote, essayez le mode reseau hote :
```
docker compose -f docker-compose-alt.yml up -d --build
```
(meme adresse http://IP:7000 ; arretez d'abord la version standard avec
`docker compose down`). Testez avant en 30 secondes que ce mode fonctionne
sur votre machine : `docker run --rm --network host hello-world`.

## Si YouTube bloque votre serveur (message "YouTube a bloque votre serveur")

1. Connectez-vous a YouTube dans votre navigateur.
2. Installez l'extension "Get cookies.txt LOCALLY" et exportez le fichier.
3. Placez `cookies.txt` a cote du docker-compose.yml et decommentez les 2
   lignes marquees dans la section cookies (volumes + environment).
4. Relancez `docker compose up -d --build`.

## Arreter / mettre a jour

- Arreter : `docker compose down`
- Mettre a jour : `docker compose up -d --build` apres avoir remplace les fichiers.

Votre musique est conservee dans votre dossier Musique ; les favoris et
playlists sont dans le volume Docker "resonance-data" (rien ne se perd).
