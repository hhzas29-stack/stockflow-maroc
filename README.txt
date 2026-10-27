STOCKFLOW MAROC - VERSION COMPLETE

Contenu :
- Tableau de bord avec indicateurs, état du stock, produits à surveiller et derniers mouvements.
- Gestion des produits : ajout, recherche, filtres, tri, modification et suppression.
- Import en lot de produits depuis Excel (.xlsx/.xls) ou CSV.
- Modèle Excel téléchargeable pour préparer un import.
- Mouvements de stock : entrées, sorties, historique et filtres.
- Export CSV et Excel de l'inventaire et de l'historique des mouvements.
- Sauvegarde Excel complète depuis la barre latérale (inventaire + mouvements).
- Validation des quantités/prix et prévention des sorties supérieures au stock disponible.

INSTALLATION / PUBLICATION
1. Décompresser le ZIP.
2. Dans le dépôt GitHub relié à Streamlit, remplacer app.py et requirements.txt par ceux de ce dossier.
3. Valider les changements avec Commit changes.
4. Attendre la fin du redéploiement Streamlit Cloud puis recharger l'application.

LANCEMENT LOCAL
pip install -r requirements.txt
streamlit run app.py

IMPORTANT - DONNEES
L'application utilise SQLite dans stockflow.db. Sur un hébergement gratuit dont le disque local n'est pas persistant, les données peuvent disparaître lors d'un redémarrage/redéploiement. Télécharger régulièrement la sauvegarde Excel depuis la barre latérale.
