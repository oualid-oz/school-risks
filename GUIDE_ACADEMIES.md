# Ouvrir School Risk Tracker dans une académie

Guide pour les personnes qui mettent en service l'outil dans une académie ou un département :
conseillers techniques, chargés de mission risques majeurs, services informatiques.

## Ce qu'est l'outil, et ce qu'il n'est pas

School Risk Tracker rassemble, pour chaque collège, les risques auxquels il est exposé :
inondation, argiles, mouvements de terrain, industriel, transport de matières dangereuses,
nucléaire, et selon le territoire séisme, radon, feu de forêt, littoral et avalanche.
Il suit aussi les PPMS, les exercices et les actions de prévention.

Les scores servent à **prioriser** le suivi. Ce n'est pas une classification officielle, et
ils ne remplacent ni le PPMS, ni le DDRM, ni le DICRIM.

## Avant d'ouvrir un territoire

1. **Regarder la page « Qualité des données »** (menu du compte, administrateurs). Pour chaque département, elle indique :
   - ce que chaque source couvre ;
   - les collèges incomplets ;
   - l'origine des secteurs.

   Hors Loiret, deux données manquent partout :
   - **Transport de matières dangereuses** : aucune source publique nationale ;
   - **Repères de crue** : l'export national est limité.

   Les fiches de ces collèges portent la mention « évaluation incomplète », et leurs scores sont des **minima**. Décidez en connaissance de cause, et dites-le aux utilisateurs.
2. **Vérifier les secteurs.** Hors départements qui publient leur propre carte (le Loiret), ils viennent de la carte scolaire nationale. Une commune partagée entre plusieurs collèges y est découpée de façon approchée. Huit départements n'ont pas de carte scolaire publiée : 09, 12, 17, 31, 32, 46, 81 et 82. Si votre département publie sa carte des secteurs, signalez-le à l'équipe technique : elle peut être branchée comme celle du Loiret.
3. **Relire le calibrage.** La page « Simulation » montre l'effet des poids et des seuils sur vos collèges. Un changement de paramètres vaut pour toute la France : il se décide avec l'équipe nationale, pas département par département.
4. **Préparer la liste des comptes** :
   - un ou deux administrateurs pour l'académie ou pour chaque département ;
   - les référents de chaque collège (chef d'établissement, adjoint, assistant de prévention) ;
   - des lecteurs, si besoin.

## Les rôles

| Rôle | Périmètre | Ce qu'il fait |
|---|---|---|
| Administrateur national | France entière | Tout, y compris ouvrir les comptes des académies |
| Administrateur d'académie ou de département | Ses départements | Gère les comptes, les actions et les PPMS de son périmètre ; traite les signalements |
| Référent | Ses collèges | Tient à jour le PPMS, les exercices et les actions de ses collèges |
| Lecteur | Un territoire ou la France | Consulte ; peut signaler une erreur de données |

L'administrateur national crée le compte de l'administrateur d'académie, avec le
« Périmètre » de l'académie. Celui-ci crée ensuite les autres comptes de son territoire.
Chacun ne voit que son périmètre.

## La première semaine

- **Les référents** : chacun se connecte, change son mot de passe (« Mon compte »), renseigne son adresse e-mail et active les alertes s'il le souhaite.
- **Les PPMS** : les référents saisissent la date de la dernière mise à jour du PPMS et les exercices de l'année. Tant que ce n'est pas fait, le tableau de bord affiche « PPMS non renseigné ».
- **Les actions** : les administrateurs créent les premières actions de suivi pour les collèges au risque « élevé », puis les assignent.
- **Le récapitulatif hebdomadaire** : il part le lundi matin vers les personnes qui ont activé les alertes. Il liste les actions en retard ou proches de leur échéance, et les PPMS à mettre à jour.

## Le retour vers l'équipe nationale

- **Signalements d'erreurs** : sur chaque fiche collège, « Signaler une erreur de données » (position, effectif, secteur, risque…). Les administrateurs du territoire les traitent dans « Qualité des données » et répondent. La correction passe ensuite par la source officielle (annuaire de l'Éducation nationale, Géorisques…), car les données sont reconstruites chaque mois à partir des sources.
- **Point mensuel** pendant les trois premiers mois, entre l'équipe nationale et les administrateurs des premiers territoires. On y passe en revue :
  - les signalements ouverts ;
  - les collèges dont le niveau paraît faux sur le terrain ;
  - les besoins non couverts.

  Les écarts de niveau alimentent la revue du calibrage.
- **Contact** : [À compléter : adresse de l'équipe nationale].

## Documents utiles

- Données personnelles, mentions légales, accessibilité : liens en bas de chaque page.
- Pour l'équipe technique : `README.md` (installation, données, paramètres) et `deploy/rgpd.md` (données personnelles, hébergement).
