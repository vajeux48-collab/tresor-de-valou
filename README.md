# Le Trésor de Valou — tirage en ligne

Application Python basée sur serveur_tirage.py, avec QR code, attribution en temps réel et page organisateur protégée.

## Hébergement Render

1. Ouvrir https://dashboard.render.com/ et connecter GitHub.
2. Choisir **New > Blueprint** et le dépôt vajeux48-collab/tresor-de-valou.
3. Vérifier le prix du plan Starter et du disque persistant (payants), puis confirmer le déploiement.
4. Ouvrir l'URL Render pour les joueurs et /organisateur pour l'organisateur.
5. Le secret administrateur est la variable TIRAGE_ADMIN_PASSWORD dans le tableau de bord Render.

Les inscriptions sont stockées dans /var/data/tirage_etat.json. GitHub Pages ne peut pas exécuter Python. Le serveur doit être hébergé en ligne pour que les téléphones puissent participer. Un navigateur réinitialisé ou un autre téléphone peut permettre un second tirage : ce n'est pas une identification personnelle forte.
