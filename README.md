# StockFlow Maroc

Application Streamlit de gestion de stock, pensée pour les petites entreprises au Maroc. Interface en français, montants en DH.

## Fonctions
- Dashboard : références, unités, valeur d'achat, vente potentielle, marge estimée.
- Graphique Top 5 des produits par valeur de stock.
- Courbe du bénéfice brut estimé des sorties enregistrées.
- Gestion des produits : ajout, modification, suppression, seuil d'alerte et fournisseur associé.
- Mouvements d'entrée/sortie : le stock est ajusté automatiquement et l'historique est conservé.
- Gestion des fournisseurs : ajout, modification, suppression, coordonnées et produits liés.
- Alertes de stock faible / rupture et détection du stock dormant (30 jours).
- Exports CSV et sauvegarde Excel multi-feuilles.
- Base SQLite créée automatiquement au premier lancement.

## Déployer sur Streamlit Community Cloud
1. Décompressez ce ZIP sur votre ordinateur.
2. Dans le dépôt GitHub voulu, téléversez `app.py`, `requirements.txt`, `README.md` et le dossier `.streamlit` (avec `config.toml`) à la racine.
3. Cliquez **Commit changes**.
4. Dans Streamlit Community Cloud, vérifiez que le dépôt, la branche et le chemin principal correspondent au dépôt et à `app.py`.
5. Redémarrez l'application si nécessaire.

## Lancer sur Windows en local
Installez Python 3.10 ou plus récent. Ouvrez un terminal dans ce dossier puis exécutez :

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Vous pouvez aussi double-cliquer sur `run_windows.bat` après avoir installé Python.

## Important concernant les données
La base `stockflow.db` est créée automatiquement à côté de `app.py`. En local, elle conserve les données tant que le fichier n'est pas supprimé. Sur Streamlit Community Cloud, le stockage local n'est pas garanti persistant après certains redémarrages ou redéploiements. Téléchargez régulièrement la sauvegarde Excel dans l'application. Pour une production avec persistance fiable, il faut connecter une base de données externe persistante (par exemple PostgreSQL); ce projet n'est pas encore configuré pour cette connexion.

## Notes
- Le bénéfice affiché est une estimation basée sur le prix de vente enregistré à la sortie moins le prix d'achat actuel du produit. Si le prix d'achat change, les anciennes estimations peuvent ne pas représenter le coût historique.
- N'envoyez jamais de mots de passe ou secrets dans GitHub.
