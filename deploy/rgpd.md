# School Risk Tracker : données personnelles et hébergement

Document de travail pour le responsable du traitement et son délégué à la protection des
données (DPO). Il décrit ce que l'application traite et ce qu'elle applique déjà. Les
décisions restent à prendre : elles sont listées à la fin.

## Le traitement

- **Finalité** : suivre l'exposition des collèges aux risques naturels et technologiques et les actions de prévention (PPMS, exercices, actions de suivi), et prévenir les personnes chargées de ce suivi.
- **Personnes concernées** : les utilisateurs (administrateurs, référents, lecteurs) et les personnes qui signalent une erreur de données. Les données sur les collèges sont publiques et ne concernent pas des personnes.
- **Base légale** : à déterminer, probablement l'article 6.1.e du RGPD (mission d'intérêt public).

## Données et durées de conservation

| Données | Où | Conservation | Appliqué par l'application |
|---|---|---|---|
| Compte : identifiant, rôle, périmètre, e-mail (facultatif), choix des alertes, mot de passe chiffré (Argon2, irréversible) | `app_user`, `user_departement`, `user_college` | Tant que la fonction le justifie | Désactivation par un administrateur ; pas de suppression automatique |
| Journal de sécurité : connexions, créations, modifications, exports, avec identifiant et adresse IP ; texte des commentaires supprimés | `audit_log` | 12 mois | Oui : `SRT_AUDIT_RETENTION_MONTHS`, purge quotidienne |
| Envois d'e-mails : destinataire, date, erreur éventuelle | `notification_log` | 6 mois | Oui : `SRT_NOTIFICATION_RETENTION_MONTHS`, purge quotidienne |
| Actions, commentaires, PPMS, exercices, signalements, avec le nom de leur auteur | `action`, `action_event`, `ppms_*`, `data_report` | Tant qu'utiles au suivi | Non |
| Journaux techniques du serveur (requêtes, sans corps ni mots de passe) | Hébergeur | Selon l'hébergeur | Non |

La purge tourne avec le récapitulatif hebdomadaire : toutes les heures dans le conteneur `scheduler` (Docker), chaque jour avec la tâche planifiée Vercel. On peut aussi la lancer à la main : `python -m app.manage purge`.

## Sécurité déjà en place

- **Accès** : comptes nominatifs, rôles et périmètres (un administrateur de département ne voit que son département), mots de passe d'au moins 12 caractères chiffrés en Argon2.
- **Jetons** : jetons de session de 30 minutes, révoqués quand le mot de passe change.
- **Protections** : limitation du nombre de tentatives de connexion et journal de sécurité.
- **Données transmises** : HTTPS via le proxy de l'hébergeur ; aucun cookie de mesure d'audience ni de publicité ; les rapports d'erreurs (Sentry, facultatif) sont expurgés des données personnelles avant l'envoi.
- **Sauvegardes** : quotidiennes, conservées 14 jours (Docker).

## Sous-traitants et transferts

| Service | Rôle | Dans la configuration actuelle | Remarque |
|---|---|---|---|
| Vercel | Hébergement de l'application | Utilisé (Loiret) | Société américaine |
| Neon | Base de données PostgreSQL | Utilisé (Loiret) | Société américaine |
| Upstash (Redis) | Compteurs de limitation de débit | Facultatif | Société américaine |
| Sentry | Alertes d'erreurs | Facultatif, désactivé sans `SRT_SENTRY_DSN` | Société américaine, région UE disponible |
| Serveur SMTP | Envoi des récapitulatifs | Celui configuré | À choisir |
| OpenStreetMap, unpkg.com, cdn.jsdelivr.net, api-adresse.data.gouv.fr | Fonds de carte, bibliothèques, recherche d'adresse | Appelés directement par le navigateur | Reçoivent l'adresse IP des visiteurs |

Pour un déploiement national d'un service public, la recommandation est un hébergement en France ou dans l'Union européenne, chez un hébergeur soumis au seul droit européen. L'application tourne telle quelle en Docker Compose (PostgreSQL avec PostGIS, Redis) chez tout hébergeur qui fournit une machine virtuelle ou une base PostgreSQL gérée avec l'extension PostGIS : par exemple OVHcloud, Scaleway ou Clever Cloud, dont les offres sont à vérifier au moment du choix.

Taille pour la France entière, mesurée en novembre 2026 : environ 4 vCPU et 8 Go de mémoire pour l'application (4 processus de 370 Mo), et une base PostgreSQL d'environ 2 Go.

## À décider par le responsable du traitement

1. La base légale, et l'inscription au registre des traitements.
2. L'hébergeur (voir ci-dessus), puis les contrats de sous-traitance (article 28) avec chaque sous-traitant retenu.
3. Le délai de suppression des comptes désactivés et la durée de conservation des actions, commentaires et signalements.
4. Les durées du journal de sécurité (12 mois par défaut) et des envois d'e-mails (6 mois), à confirmer.
5. Les textes des pages publiques, où toutes les mentions entre crochets sont à compléter : `frontend/donnees-personnelles.html`, `frontend/mentions-legales.html` et `frontend/accessibilite.html`.
6. L'audit d'accessibilité RGAA. Sans audit, la déclaration doit indiquer « non conforme », ce qu'elle fait.
7. S'il faut une analyse d'impact (AIPD) : a priori non, car le traitement porte sur des comptes professionnels et non sur des données sensibles. À confirmer avec le DPO.
