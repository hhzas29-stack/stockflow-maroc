# StockFlow Maroc

Application Streamlit de gestion de stock pour petites entreprises : produits, fournisseurs, mouvements d'entrée/sortie, alertes, dashboard et exports Excel/CSV. Interface en français, montants en dirhams marocains (DH).

## Lancer sous Windows

1. Installez Python 3.10 ou plus récent.
2. Ouvrez ce dossier dans VS Code ou un terminal.
3. Exécutez `pip install -r requirements.txt`.
4. Exécutez `streamlit run app.py`.

Vous pouvez aussi double-cliquer sur `run_windows.bat` après l'installation de Python.

## Déployer sur Streamlit Community Cloud

1. Envoyez `app.py`, `requirements.txt`, `README.md` et `.streamlit/config.toml` vers un dépôt GitHub.
2. Dans Streamlit Community Cloud, créez ou redéployez l'application en choisissant `app.py` comme Main file path.
3. Après chaque mise à jour, vérifiez les logs de déploiement.

## Données et sauvegardes

Les données sont stockées dans `stockflow.db` à côté du script par défaut. Sur Streamlit Community Cloud, le stockage local n'est pas garanti persistant après redémarrages ou redéploiements. Utilisez les exports Excel régulièrement et, pour une utilisation commerciale avec données persistantes, migrez vers une base externe persistante (par exemple PostgreSQL managé). Ne mettez jamais de mots de passe ou secrets dans GitHub.

## Limites à connaître

- Le graphique de bénéfice brut est estimatif et utilise le prix d'achat actuel du produit; il ne remplace pas une comptabilité ou une valorisation historique des coûts.
- Testez l'application avec des données d'exemple avant de la livrer à un client.
