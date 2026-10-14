# PyFinance

Gestion de finances personnelles : comptes, opérations, compte titres (PMPA, plus-values),
budget prévisionnel sur 5 ans (réel vs prévu), recherche multicritère et rapports.

Django 5.2 · PostgreSQL ou SQLite · Tailwind CSS · HTMX · Lucide.

## Démarrage

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py donnees_demo        # utilisateur « demo » / « demo », 18 mois d'historique (facultatif)
python manage.py createsuperuser     # accès à /admin/
python manage.py runserver
```

Les nouveaux inscrits reçoivent un jeu de catégories par défaut
(`python manage.py seed_categories <utilisateur>` pour un compte existant).

## Configuration

| Variable | Rôle |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | Active PostgreSQL (sinon SQLite) |
| `SQLITE_PATH` | Chemin du fichier SQLite (défaut : `db.sqlite3`) |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | Réglages de production |

## Tests

```bash
python manage.py test tests
```

## Production : Tailwind

En développement, Tailwind est chargé par le « Play CDN » (compilation dans le navigateur).
En production, compiler une feuille statique avec le CLI Tailwind en scannant
`templates/**/*.html`, `core/forms.py` et `finances/templatetags/*.py` (classes générées côté Python),
puis remplacer le `<script src="https://cdn.tailwindcss.com…">` de `templates/base.html`
par un `<link>` vers la feuille compilée.

## Raccourcis

`N` nouvelle opération · `/` recherche · `Ctrl + Entrée` enregistrer le formulaire.
