# Audit SEO automatisé

Crawle un site, applique une quarantaine de contrôles SEO techniques et produit un rapport HTML filtrable, deux CSV et un JSON complet.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Options facultatives :

```bash
pip install playwright && playwright install chromium   # --render-js
pip install google-auth requests                         # --gsc-site
```

## Utilisation

```bash
# Audit de base
python -m seo_audit https://www.example.com/

# Audit complet
export PSI_API_KEY=...                                     # clé Google Cloud, API PageSpeed Insights activée
export GOOGLE_APPLICATION_CREDENTIALS=compte-service.json
python -m seo_audit https://www.example.com/ \
  --max-pages 2000 --check-external \
  --pagespeed 20 --gsc-site sc-domain:example.com --gsc-max 200
```

Le rapport est écrit dans `rapports/<site>-<date>/` : `rapport.html`, `problemes.csv`, `pages.csv` (séparateur `;`, s'ouvre directement dans Excel) et `audit.json`.

Pour l'intégration continue, `--fail-on high` renvoie le code de sortie 1 dès qu'un problème de gravité élevée ou critique est trouvé.

## Vigie SEO, la version en ligne

Le même moteur d'audit existe en application web multi-utilisateurs (Vercel + Supabase), avec connexion Google :

- **Projets** : score par site, historique, problèmes groupés par type, comparaison avec l'audit précédent, export CSV et HTML.
- **Positions** : requêtes Search Console de chaque utilisateur (lecture seule), importées puis mises à jour chaque jour.
- **Mots-clés** : idées issues de l'autocomplétion de Google et des requêtes réelles.
- **Concurrents** : exploration légère et comparaison avec votre dernier audit.
- **Offres** : quotas par plan (gratuit, pro, agence) dans `seo_audit/cloud/plans.py`.

Les audits avancent par étapes de 40 secondes (`seo_audit/engine/`), relancées chaque minute par Supabase : c'est ce qui
permet d'explorer des milliers de pages avec des fonctions Vercel limitées dans le temps. Chaque page visitée passe par
un garde-fou anti-SSRF (`seo_audit/ssrf.py`), et chaque utilisateur ne voit que ses données.

Mise en ligne pas à pas : [DEPLOIEMENT.md](DEPLOIEMENT.md).

## Ce qui est contrôlé

| Catégorie | Contrôles |
|---|---|
| HTTP | Erreurs 4xx et 5xx, pages injoignables, chaînes et boucles de redirection, redirections temporaires, temps de réponse serveur (TTFB) |
| Indexabilité | noindex (meta et en-tête X-Robots-Tag), robots.txt, canonical absente, multiple ou pointant vers une page non indexable, URLs du sitemap non 200, en noindex ou bloquées |
| Titres et métadonnées | Title absent, multiple, trop court ou trop long, dupliqué ; meta description idem ; H1 absent ou multiple ; images sans alt ; contenu faible |
| Liens | Liens internes et externes cassés, liens vers des redirections, pages orphelines (dans le sitemap, sans lien interne), pages trop profondes |
| Technique | Attribut lang, meta viewport ; avec `--render-js`, éléments SEO qui n'existent qu'après exécution du JavaScript |
| International | Codes hreflang invalides, conflits, auto-référence absente, liens retour manquants, cibles non indexables |
| Données structurées | JSON-LD illisible, `@type` absent, propriétés requises manquantes (Article, Product, FAQPage…) |
| Contenu dupliqué | Contenu identique (empreinte) ou quasi identique (simhash) entre pages indexables |
| Performance | Score Lighthouse ; LCP, INP et CLS terrain (données CrUX au 75e centile) |
| Search Console | Verdict d'indexation réel, canonical retenue par Google différente de la canonical déclarée |

Les seuils (longueur des titles, profondeur maximale, TTFB…) sont regroupés en tête de `seo_audit/checks.py`.

## Scores et comparaison

Le rapport donne un score de santé global et un score par catégorie. À chaque exécution, l'audit est comparé au dernier audit du même site présent dans `rapports/` (problèmes nouveaux, corrigés, évolution des scores). Options : `--compare chemin/audit.json` pour choisir la référence, `--no-compare` pour désactiver.

## Notes de fonctionnement

Le crawler ne suit pas les redirections automatiquement : chaque saut est enregistré comme une URL distincte, ce qui permet de reconstituer les chaînes. Les URLs sont normalisées par `seo_audit/urls.py` (paramètres UTM retirés, `www.` considéré comme le même site) ; c'est cette même fonction qu'il faudra réutiliser pour joindre les données Search Console et GA4 dans le tableau de bord.

Les liens externes en 401, 403 ou 429 ne sont pas comptés comme cassés, car beaucoup de sites renvoient ces codes aux robots alors que la page fonctionne.

Les performances et l'inspection Search Console portent sur les pages indexables les plus liées en interne, accueil en premier. L'API URL Inspection est limitée à environ 2 000 requêtes par jour et par propriété. Le compte de service doit être ajouté comme utilisateur de la propriété dans Search Console.

`--lighthouse-local` lance Lighthouse via `npx` (Node.js et Chrome requis) au lieu de l'API PageSpeed ; les mesures sont faites une page à la fois pour ne pas se fausser mutuellement.

## Tests

```bash
python -m pytest
```

Les tests démarrent un petit site local volontairement défectueux (`tests/site_de_test.py`) et vérifient que chaque problème attendu est détecté. Ce site peut aussi être lancé seul avec `python -m tests.site_de_test` pour essayer l'outil.
