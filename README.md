# StockFlow Maroc — version Supabase Auth + PostgREST/RLS

Application Streamlit de gestion de stock : produits, fournisseurs, mouvements, dashboard et export Excel.

## Sécurité de la base

Cette version n'utilise **plus `DATABASE_URL` ni une connexion PostgreSQL privilégiée**. Toutes les lectures/écritures passent par Supabase PostgREST avec le jeton d'accès de l'utilisateur connecté. Supabase valide le JWT avant que PostgreSQL applique les politiques RLS. Les opérations multi-étapes « créer produit + stock initial » et « mouvement + quantité » sont des RPC `SECURITY INVOKER` afin de rester transactionnelles sous les droits/RLS de l'utilisateur.

## Avant déploiement — important

1. Faites une sauvegarde de la base Supabase et des données existantes.
2. Dans Supabase > SQL Editor, examinez puis exécutez `migrations/001_auth_rls.sql`.
3. Ensuite examinez puis exécutez `migrations/002_rest_rpc.sql`.
4. Les anciennes lignes sans `user_id` restent volontairement non attribuées et invisibles. Ne les attribuez qu'après avoir vérifié leur propriétaire et effectué une sauvegarde.
5. Dans Streamlit Cloud > Settings > Secrets, configurez seulement :

```toml
SUPABASE_URL = "https://VOTRE-PROJET.supabase.co"
SUPABASE_ANON_KEY = "VOTRE_CLE_PUBLIQUE_ANON_OU_PUBLISHABLE"
```

N'ajoutez jamais une clé `service_role` / `sb_secret_` à Streamlit. `DATABASE_URL` n'est plus utilisé par cette version.

6. Vérifiez dans Supabase Auth les paramètres d'inscription et de confirmation email. Testez avec deux comptes distincts : chaque compte doit voir uniquement ses propres données; créez un fournisseur/produit/mouvement dans A, puis vérifiez qu'ils sont invisibles dans B. Testez aussi les exports Excel et les mouvements Entrée/Sortie.

## Exécution locale

```bash
pip install -r requirements.txt
streamlit run app.py
```

Les tests de compilation Python ne remplacent pas les tests d'intégration sur votre projet Supabase. Ne déployez pas en production avant d'avoir exécuté les deux migrations et testé l'isolation avec deux comptes.

## Vérifications locales incluses

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py supabase_auth.py
```

Ces tests vérifient la configuration de clé, le rejet des clés `service_role` (y compris les anciens JWT dont le claim `role` est `service_role`) et les limites de session. Ils ne testent pas votre base Supabase réelle et ne prouvent pas à eux seuls l'isolation entre comptes.

## Migration sur une base existante

Ne lancez pas les migrations sans sauvegarde. Le script 001 traite maintenant le cas où les anciens index uniques sont associés à des contraintes `UNIQUE`, mais d'autres différences de schéma ou des données incohérentes peuvent encore faire échouer une migration. Les lignes historiques sans `user_id` restent volontairement non attribuées et invisibles. N'utilisez pas de script de rattachement sans avoir choisi explicitement le compte propriétaire.
