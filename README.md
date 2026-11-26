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

## Ecoute mobile, minuteur de veille et statistiques (nouveau)

- **Contrôles sur l'écran verrouillé (API Media Session)** : sur téléphone,
  la notification système affiche désormais le titre, l'artiste et la
  pochette du morceau en cours ; les boutons du casque, du Bluetooth et de
  l'écran verrouillé pilotent lecture, pause, piste précédente/suivante et
  déplacement dans le morceau (curseur de progression compris). Fonctionne
  aussi pour les titres écoutés en ligne. C'est l'amélioration la plus
  visible quand l'application est installée sur l'écran d'accueil : la
  lecture ne s'arrête plus quand le navigateur passe en arrière-plan.
- **Minuteur de veille** : le bouton lune de la barre du lecteur propose
  15, 30 ou 60 minutes, ou la fin du titre en cours. Le temps restant
  s'affiche en permanence à côté du bouton ; sur les vingt dernières
  secondes le volume descend en fondu, puis la lecture se met en pause
  (aucun son brutal au réveil). Le compte à rebours survit à un
  rechargement de la page ; un bouton Annuler apparaît dans le minuteur.
- **Statistiques d'écoute — Les plus écoutées** : chaque titre local
  écouté jusqu'au bout incrémente son compteur (donnée petite, gardée
  dans la base du serveur, elle ne quitte jamais votre machine). La
  nouvelle vue « Les plus écoutées » du menu latéral classe les titres du
  plus écouté au moins écouté (la recherche et le tri habituels y
  fonctionnent), et un compteur discret (icône casque) apparaît sur
  chaque ligne de la bibliothèque. Supprimer un titre ou remplacer un
  doublon nettoie son compteur ; l'écoute en ligne n'est comptée qu'une
  fois le titre téléchargé dans la bibliothèque.
- **Minuteur au clavier** : la touche **V** ouvre directement le minuteur
  de veille (elle figure dans l'aide-mémoire **?**).

## Radio, fondu enchaine, visualiseur, playlists et sauvegarde (nouveau)

- **File d'attente reordonnable** : glissez-deposez les lignes du panneau
  « File » pour réorganiser l'ordre d'écoute ; la piste en cours reste
  surlignée (fond doré) et l'index de lecture suit le déplacement.
- **Radio automatique** : le bouton antenne d'une ligne construit depuis
  ce titre une suite continue — d'abord les titres du même artiste, puis
  du même album dans votre bibliothèque ; quand le local s'épuise, une
  recherche en ligne complète la file (12 titres comparables) et la
  lecture repart toute seule. Un bandeau « Radio » dans le panneau de
  file indique qu'elle est active et permet de l'éteindre. La file reste
  réordonnable et visible comme d'habitude. Hors ligne, la radio
  s'arrête proprement à la fin des titres locaux.
- **Fondu enchaîné** : la touche **B** règle le croisement entre titres
  (désactivé, 2, 4 ou 6 secondes — réglage mémorisé, rappelé par une
  notification à chaque appui). Les dernières secondes du titre courant
  s'effacent pendant que le suivant monte : la piste suivante est
  préchargée dans une seconde source audio, puis le lecteur principal
  prend le relais exactement à la position atteinte — paroles, pochette,
  statistiques et écran verrouillé suivent sans à-coup. Un déplacement
  manuel ou un changement de titre annule le fondu en cours ; il est
  suspendu quand le minuteur de veille est réglé sur « fin du titre ».
- **Visualiseur spectral** : actif dès l'arrivée (il se met à danser au
  premier clic, sans jamais couper le son au démarrage) — fines barres
  dorées derrière les commandes du lecteur, branchées sur un analyseur
  réel du contexte audio de l'égaliseur. La touche **W** (ou **Z** —
  leur position physique s'échange entre QWERTY et AZERTY) le coupe et
  le réactive ; le dessin est plafonné à ~30 images/s et s'arrête quand
  rien ne joue, pour ménager la batterie des téléphones. Le réglage est
  mémorisé.
- **Raccourcis AZERTY** : les lettres de raccourci suivent ce qui est
  imprimé sur le clavier, pas la position physique QWERTY — sur AZERTY,
  la touche **M** coupée du son, la touche **W** (et **Z**) du
  visualiseur, etc., fonctionnent telles qu'étiquetées.
- **Import de playlist YouTube** : collez dans la recherche YouTube un
  lien contenant `list=` — la liste s'ouvre avec des cases à cocher (les
  titres déjà présents sont décochés et marqués « déjà présent »).
  « Télécharger la sélection » met chaque titre coché en file
  d'attente d'arrière-plan, dans l'ordre.
- **Exploration en ligne sans fin** : la liste affiche 40 titres par
  page (tendances et recherches, YouTube comme Audius) et une nouvelle
  page se charge automatiquement quand on approche du bas — dédoublon­
 nage inclus, arrêt propre quand la source n'a plus rien.
- **Qualité de téléchargement** : le panneau des téléchargements offre
  128, 192 (défaut) ou 320 kbit/s pour les conversions YouTube —
  économie de place ou fidélité. Le choix est mémorisé et s'applique à
  chaque téléchargement, y compris les imports de playlist.
- **En-tête allégé** : le bouton « dossier de musique surveillé » a été
  retiré ; le tri A-Z/Z-A tient désormais dans une flèche d'un seul
  clic (l'ancien menu déroulant a disparu), et une icône de menu (⋯) à
  droite regroupe les actions secondaires : Téléchargements en cours
  (avec le badge d'avancement), Stockage hors ligne, Sauvegarde /
  restauration.
- **Sauvegarde / restauration** : dans le menu (⋯) de l'en-tête,
  « Sauvegarde / restauration » exporte en un fichier JSON vos favoris,
  playlists et statistiques — importable tel quel sur un nouveau
  serveur, sans toucher au dossier de musique ni aux fichiers audio.
- **Résonance du serveur** : fini la pastille d'état. Sous le titre de
  la vue, des ondes concentriques se propagent depuis une source — le
  nom de l'app rendu littéral. Ondes dorées régulières : le serveur a
  Internet ; ambre qui vacille : catalogue en ligne limité ; grise et
  lente : sondage en cours ; rouges qui se replient vers la source :
  connexion perdue — la bibliothèque locale, elle, continue de jouer
  sans attendre. Survolez les ondes pour le détail.
- **Accès protégé par code (optionnel)** : définissez la variable
  d'environnement `RESONANCE_ACCESS_CODE` (exemple commenté dans
  docker-compose.yml) et l'application exige ce code une fois par
  navigateur (cookie valable 30 jours) — utile dès que le serveur
  devient joignable depuis l'extérieur du réseau local, puisque sans
  cela n'importe qui du réseau peut lancer des téléchargements ou
  modifier la bibliothèque. Les essais ratés sont ralentis. Pour une
  exposition publique, ajoutez un proxy inverse HTTPS (exemple Caddy :
  `resonance.mondomaine.fr reverse_proxy localhost:7000`, certificat
  automatique) : lecture automatique, notification de lecture et cadres
  YouTube se montent plus fièrement en contexte sécurisé.

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
