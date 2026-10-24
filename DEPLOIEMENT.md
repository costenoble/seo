# Mettre Vigie SEO en ligne (Vercel + Supabase)

Comptez une heure. Les étapes sont dans l'ordre : chacune a besoin de la précédente.

## 1. Supabase — base de données (déjà fait)

Les tables ont été créées avec `supabase/migrations/0001_audits.sql` et `0002_app.sql`. Pour un nouveau projet,
collez ces deux fichiers dans **SQL Editor** et exécutez-les.

Sécurité, à faire maintenant :
- **Database → Settings → Reset database password**, puis mettez à jour `DATABASE_URL` (Vercel et `.env`).
- **Project Settings → API Keys** : créez une clé secrète (`sb_secret_…`), mettez-la dans `SUPABASE_SERVICE_ROLE_KEY`,
  puis désactivez les anciennes clés JWT (« legacy »). L'ancienne clé service_role a circulé hors du coffre.

## 2. Google Cloud — connexion « Se connecter avec Google »

Sur <https://console.cloud.google.com>, avec votre compte Google habituel :

1. Créez un projet « Vigie SEO ».
2. **API et services → Bibliothèque** : activez **Google Search Console API**.
3. **Écran de consentement OAuth** (ou « Google Auth Platform ») :
   - Type **Externe**, nom « Vigie SEO », logo `public/img/vigie-mark.svg` (exporté en PNG 120×120), e-mail d'assistance.
   - Domaine autorisé : `vigieseo.fr` (et `vercel.app` en attendant), lien vers `/confidentialite.html`.
   - Champs d'application : `email`, `profile`, `openid`, `…/auth/webmasters.readonly`.
   - **Utilisateurs test** : ajoutez votre adresse et celles de vos testeurs (100 au maximum avant validation).
4. **Identifiants → Créer → ID client OAuth → Application Web** :
   - Origines JavaScript autorisées : `https://VOTRE-DOMAINE`, `http://localhost:3000`.
   - URI de redirection autorisée : `https://kpqzrmksmqaylzkcpetj.supabase.co/auth/v1/callback`
   - Notez l'**ID client** et le **code secret**.

## 3. Supabase — activer Google

1. **Authentication → Sign In / Providers → Google** : activez, collez l'ID client et le code secret.
2. **Authentication → URL Configuration** :
   - Site URL : `https://VOTRE-DOMAINE`
   - Redirect URLs : `https://VOTRE-DOMAINE/**`, `https://*-costenoble.vercel.app/**` (prévisualisations), `http://localhost:3000/**`

## 4. Vercel — déploiement

1. <https://vercel.com/new> → importez le dépôt GitHub `costenoble/seo`. Framework : **Other**. Ne changez rien d'autre
   (`vercel.json` règle le dossier `public/`, la fonction Python et la tâche planifiée).
2. **Environment Variables** : recopiez toutes les lignes de votre `.env` (voir `.env.example`), dont
   `GOOGLE_CLIENT_ID` et `GOOGLE_CLIENT_SECRET` de l'étape 2. `DATABASE_URL` doit utiliser le **pooler, port 6543**.
3. Déployez, puis ouvrez `https://VOTRE-PROJET.vercel.app/api/config` : `"missing": []` doit s'afficher.

## 5. Supabase — planificateur (audits en continu)

Vercel gratuit ne lance une tâche planifiée qu'une fois par jour. Supabase prend le relais chaque minute :
ouvrez `supabase/cron.sql`, remplacez `VOTRE-DOMAINE` et `VOTRE_CRON_SECRET`, exécutez-le dans **SQL Editor**.
Sans lui, un audit n'avance que tant que l'utilisateur garde la page ouverte.

## 6. Nom de domaine

`vigieseo.fr`, `vigieseo.com` et `vigieseo.io` étaient libres le 24 octobre 2026 (vérification whois, à confirmer à l'achat).
Achetez au moins le `.fr` et le `.com` (OVH, Gandi, Cloudflare : environ 10 € par an chacun), puis **Vercel → Settings →
Domains**, et suivez les enregistrements DNS indiqués. Vérifiez aussi la marque sur <https://data.inpi.fr>.
Ensuite, mettez à jour la Site URL Supabase et les origines Google avec le nouveau domaine.

## 7. Validation Google (avant l'ouverture au public)

Tant que l'application est « en test », seuls vos utilisateurs test peuvent se connecter, et leur connexion à la
Search Console expire tous les 7 jours. Pour ouvrir à tous : **Écran de consentement → Publier l'application**,
puis demande de validation (champ d'application sensible) avec une vidéo montrant l'écran Positions. Comptez
quelques semaines. La page `/confidentialite.html` est un modèle : faites-la compléter et relire.

## Gérer les offres (en attendant le paiement en ligne)

Dans **SQL Editor** : `update profiles set plan = 'pro' where email = 'client@exemple.fr';` (valeurs : `gratuit`, `pro`, `agence`).
Les limites de chaque offre sont dans `seo_audit/cloud/plans.py`.

## Développement local

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
set -a; . ./.env; set +a
.venv/bin/python -m seo_audit serve          # http://localhost:3000
.venv/bin/python -m pytest                   # nécessite PostgreSQL installé pour les tests de base de données
```
