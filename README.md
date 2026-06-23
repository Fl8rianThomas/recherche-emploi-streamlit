# Recherche d'offres sans ESN — guide d'installation

Un script qui interroge l'API officielle de France Travail, garde les offres
qui correspondent à tes mots-clés et **retire automatiquement les ESN**.

Tu pars de zéro : suis les étapes dans l'ordre.

---

## Étape 1 — Installer Python

1. Va sur https://www.python.org/downloads/ et installe la dernière version.
2. **Windows** : pendant l'installation, coche la case **« Add Python to PATH »**.
3. Pour vérifier, ouvre un terminal et tape :
   ```
   python --version
   ```
   (Sur Mac/Linux, parfois `python3 --version`.)

## Étape 2 — Mettre les fichiers dans un dossier

1. Crée un dossier, par ex. `recherche-emploi`.
2. Mets-y les 4 fichiers : `recherche_offres.py`, `requirements.txt`,
   `.env.example`, `README.md`.
3. Ouvre ce dossier dans VS Code : **Fichier → Ouvrir le dossier**.
4. Installe l'extension **Python** (icône Extensions à gauche, cherche « Python »).

## Étape 3 — Installer les dépendances

Dans VS Code, ouvre un terminal (**Terminal → Nouveau terminal**) et tape :

```
pip install -r requirements.txt
```

## Étape 4 — Obtenir tes clés d'accès à l'API (gratuit)

1. Va sur **https://francetravail.io** et crée un compte.
2. Va dans ton espace, crée une **application**.
3. Souscris à l'API **« Offres d'emploi v2 »**.
4. Tu obtiens un **identifiant client** et une **clé secrète** : note-les.

## Étape 5 — Renseigner tes clés

1. Renomme le fichier `.env.example` en **`.env`**.
2. Ouvre-le et colle ton identifiant et ta clé secrète :
   ```
   FT_CLIENT_ID=ton_identifiant
   FT_CLIENT_SECRET=ta_cle_secrete
   ```
3. Enregistre. ⚠️ Ne partage jamais ce fichier.

## Étape 6 — Lancer

Dans le terminal :

```
python recherche_offres.py
```

Le script affiche les offres retenues et crée un fichier **`offres_filtrees.csv`**
(ouvrable dans Excel / LibreOffice).

---

## Personnaliser

Tout se règle en haut de `recherche_offres.py`, dans le bloc `CONFIG` :

- **`mots_cles`** : ce que tu cherches (`"business analyst"`, ou
  `"business analyst data"`, etc.).
- **`departement`** : un code à 2 chiffres (`"75"`, `"69"`…), ou `None` pour
  toute la France.
- **`commune`** + **`distance`** : pour chercher autour d'une ville. La commune
  est un **code INSEE** (pas le code postal). Tu le trouves ici :
  https://www.insee.fr/fr/recherche/recherche-geographique
- **`type_contrat`** : `"CDI"`, `"CDD"`, ou `None` pour tous.

### Affiner le filtre ESN

- **`ESN_CONNUES`** : ajoute les noms d'ESN que tu croises. Au fil du temps, ta
  liste devient redoutable.
- **`EXCLURE_PAR_TEXTE`** : mets `False` si le filtre est trop strict et écarte
  des offres légitimes ; `True` pour traquer les ESN qui se cachent.

---

## Pour aller plus loin

- **Lancer la recherche tous les jours automatiquement** : possible via le
  Planificateur de tâches (Windows) ou `cron` (Mac/Linux). Dis-le-moi si tu veux
  cette étape.
- **Recevoir un mail avec les nouvelles offres** : faisable aussi.
