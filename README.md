# Secure Web Audio Player

Un lecteur audio web moderne, combinant une interface utilisateur fluide (Flexbox) et une architecture robuste orientée sécurité.

## Fonctionnalités
* **Interface responsive** : Lecteur ancré et liste de lecture dynamique s'adaptant parfaitement à l'écran.
* **Sécurité By Design** : Architecture pensée pour limiter la surface d'attaque côté client.

## Implémentations de Sécurité
* **Content Security Policy (CSP)** : Définition stricte des sources autorisées pour bloquer l'injection de scripts malveillants (XSS).
* **Sanitisation des données** : Nettoyage des métadonnées des pistes audio (titres, artistes) avant l'injection dans le DOM.
* **Isolation du stockage** : Gestion sécurisée des préférences utilisateurs via un `localStorage` restreint et contrôlé.
* **Veille des vulnérabilités** : Surveillance automatisée des dépendances (Dependabot).

## Installation et Utilisation
1. Cloner le dépôt
2. Lancer l'app avec la commande 'python app.py'