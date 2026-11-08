# Recherche d'offres — installer ta propre version

Une application web privée qui rassemble les offres d'emploi de **France Travail,
Adzuna, Jooble, jobup.ch et ~90 sites carrières** de grands groupes, retire les ESN
et les doublons, note chaque offre selon **ton** profil et les affiche sur une carte.
Les offres sont mises à jour **toutes les 4 h**, et tu reçois **un e-mail chaque soir
dès 17 h** avec les nouveautés.

Tout tourne en ligne, gratuitement (GitHub + Streamlit) : **rien à installer sur ton
ordinateur**. Compte environ **1 h 30** la première fois.

| Étape | Durée |
|---|---|
| 1. Créer ta copie du projet | 5 min |
| 2. Créer tes clés d'accès aux sites d'offres | 30 min |
| 3. Donner tes clés à GitHub et lancer une 1re collecte | 10 min |
| 4. Régler la recherche sur ton métier | 20 min |
| 5. Mettre l'application en ligne | 15 min |
| 6. Vérifier que tout marche | 5 min |

Il te faut seulement un **compte GitHub** (gratuit : https://github.com/signup) et
une adresse e-mail.

---

## Étape 1 — Créer ta copie du projet (5 min)

1. Accepte l'invitation GitHub reçue par e-mail : elle te donne accès **en lecture**
   au projet partagé (`Infinited-Organization/recherche-emploi`).
2. Sur la page du projet, clique sur **Fork** (en haut à droite).
3. **Owner** : choisis ton compte. **Repository name** : laisse `recherche-emploi`.
   Clique sur **Create fork**.

Ce *fork* est **ta** copie : tu y modifies ce que tu veux, sans toucher au projet
partagé. Note son nom complet, du type **`ton-pseudo/recherche-emploi`** : il servira
à l'étape 5.

## Étape 2 — Créer tes clés d'accès (30 min)

Chaque site d'offres te donne gratuitement une « clé » qui autorise l'application à
l'interroger. Aucune n'est obligatoire (les ~90 sites carrières marchent sans clé),
mais plus tu en as, plus tu as d'offres. Garde-les dans une note, à l'abri : ce sont
des mots de passe.

| Service | Où créer la clé | Ce que tu récupères | Nom à utiliser |
|---|---|---|---|
| **France Travail** | https://francetravail.io → crée un compte → crée une **application** → ajoute l'API **« Offres d'emploi v2 »** | un identifiant client et une clé secrète | `FT_CLIENT_ID`, `FT_CLIENT_SECRET` |
| **Adzuna** | https://developer.adzuna.com → **Register** | un *Application ID* et une *Application Key* | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` |
| **Jooble** | https://jooble.org/api/about → remplis le formulaire | une clé, reçue par e-mail | `JOOBLE_API_KEY` |
| **Brevo** (envoi de l'e-mail du soir) | https://www.brevo.com → compte gratuit → menu **SMTP & API** → **Clés API** → génère une clé | une clé qui commence par `xkeysib-` | `BREVO_API_KEY` |

Deux réglages obligatoires dans **Brevo**, sinon l'e-mail ne part pas :

1. **Vérifie ton adresse d'expéditeur** (menu *Expéditeurs*) : c'est l'adresse qui
   enverra l'e-mail, en général la tienne.
2. **Désactive le blocage des adresses IP inconnues** pour les clés API (menu
   *Sécurité* → *Adresses IP autorisées*). Les serveurs de GitHub changent d'adresse
   à chaque envoi : avec le blocage actif, Brevo refuse l'e-mail.

## Étape 3 — Donner tes clés à GitHub (10 min)

1. Dans **ta copie**, va dans **Settings → Secrets and variables → Actions**.
2. Pour chaque ligne du tableau, clique sur **New repository secret**, mets le nom
   exactement comme écrit, colle la valeur, puis **Add secret**.

| Nom du secret | Valeur |
|---|---|
| `FT_CLIENT_ID` | identifiant client France Travail |
| `FT_CLIENT_SECRET` | clé secrète France Travail |
| `ADZUNA_APP_ID` | Application ID Adzuna |
| `ADZUNA_APP_KEY` | Application Key Adzuna |
| `JOOBLE_API_KEY` | clé Jooble |
| `BREVO_API_KEY` | clé Brevo |
| `MAIL_FROM` | ton adresse d'expéditeur, vérifiée dans Brevo |
| `MAIL_TO` | l'adresse où recevoir l'e-mail du soir (peut être la même) |

3. Va dans l'onglet **Actions**. Sur un *fork*, les automatismes sont désactivés au
   départ : clique sur **I understand my workflows, go ahead and enable them**.
4. Clique à gauche sur **Snapshot offres (toutes les 4 h)** → **Run workflow** →
   **Run workflow**. Attends environ 4 min : une coche verte ✓ = la première collecte
   est faite. Elle se relancera ensuite toute seule toutes les 4 h.

## Étape 4 — Régler la recherche sur ton métier (20 min)

Tous tes réglages sont **en haut du fichier `recherche_offres.py`**. Pour le modifier
sans rien installer : ouvre le fichier dans ta copie sur GitHub, clique sur le
**crayon ✏️** (Edit), change les valeurs, puis **Commit changes**. Pour trouver un
réglage, fais **Ctrl + F** et tape son nom.

| Réglage | À quoi il sert | Exemple |
|---|---|---|
| `SYNONYMES_PAR_DEFAUT` | les mots-clés envoyés aux sites d'offres : ton métier et ses synonymes | `"contrôleur de gestion"`, `"business controller"` |
| `INCLUSIONS_TITRE_PAR_DEFAUT` | le **titre** de l'offre doit contenir au moins un de ces mots, sinon elle est écartée (`mot:couleur` colore la ligne) | `"contrôleur de gestion:bleu"` |
| `EXCLUSIONS_TITRE_PAR_DEFAUT` | les titres à écarter ; `mot sauf a, b` écarte le mot sauf si le titre contient aussi a ou b | `"stage"`, `"assistant sauf manager"` |
| `PROFIL_OPTIMUM` | tes compétences, avec un poids : donne le **score ⭐** de chaque offre | `"sap:4"`, `"budget:3"` |
| `DEPTS_FAVORIS_PAR_DEFAUT` | tes départements préférés 🎯 | `["69", "38"]` |
| `DOMICILE`, `RAYON_DOMICILE_KM` | ta commune et le rayon de l'onglet « autour de chez moi » | `"Lyon"`, `50` |
| `GRANDS_GROUPES_PAR_DEFAUT` | les entreprises ou secteurs que tu vises en priorité 🏢 | `"Michelin"` |

Règles d'écriture : chaque valeur est **entre guillemets** et suivie d'une
**virgule**. Majuscules et accents n'ont pas d'importance (`"Contrôleur"` trouve
aussi « CONTROLEUR »).

Après chaque modification, GitHub relance des tests automatiques (onglet
**Actions**, ligne **Tests**). Une croix rouge ✗ veut presque toujours dire qu'un
guillemet ou une virgule manque : reviens corriger le fichier.

> La carte met en avant le **domaine RH / SIRH**, spécialité de l'auteur d'origine.
> Pour mettre en avant un autre domaine (finance, logistique…), il faut adapter la
> détection RH (`RH_FORTS` et `est_domaine_rh` dans `recherche_offres.py`) : c'est
> une modification plus technique, demande de l'aide.

## Étape 5 — Mettre l'application en ligne (15 min)

**a. Créer un jeton GitHub pour l'application** (il lui permet de lire tes offres et
d'enregistrer tes favoris) :

1. Sur GitHub, clique sur ta photo → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Expiration** : 1 an. **Repository access** : *Only select repositories* →
   choisis ta copie `recherche-emploi`.
3. **Permissions** → *Repository permissions* → **Contents** : **Read and write**.
4. **Generate token**, puis copie le jeton (il commence par `github_pat_`). GitHub ne
   le réaffichera plus.

**b. Déployer sur Streamlit** :

1. Va sur https://share.streamlit.io et connecte-toi **avec ton compte GitHub**.
2. **Create app** → choisis le dépôt **`ton-pseudo/recherche-emploi`**, la branche
   **`main`** et le fichier **`app.py`**. Choisis l'adresse de ton app.
3. Ouvre **Advanced settings** → **Secrets** et colle ce bloc, en remplaçant chaque
   valeur par la tienne (garde les guillemets) :

   ```toml
   GH_REPO = "ton-pseudo/recherche-emploi"
   GH_TOKEN = "github_pat_..."
   FT_CLIENT_ID = "..."
   FT_CLIENT_SECRET = "..."
   ADZUNA_APP_ID = "..."
   ADZUNA_APP_KEY = "..."
   JOOBLE_API_KEY = "..."
   ```

4. **Deploy**. Le premier démarrage prend 2 à 3 min.

Ton dépôt étant privé, l'application est **privée** : toi seul y as accès. Pour la
montrer à quelqu'un : bouton **Share** en haut à droite de l'app → son e-mail →
**Invite**.

## Étape 6 — Vérifier (5 min)

1. **L'application** : ouvre-la. L'onglet 🗺️ **Carte** doit afficher des offres.
2. **L'e-mail** : dans ta copie, **Actions** → **Recherche quotidienne** →
   **Run workflow**. L'e-mail arrive en 2 à 3 min. Ensuite, il part tout seul chaque
   soir à partir de 17 h (un seul par jour).

---

## Si quelque chose ne marche pas

| Symptôme | Cause probable | Solution |
|---|---|---|
| L'app ne montre aucune offre | la 1re collecte n'a pas tourné, ou `GH_REPO` / `GH_TOKEN` est faux | refais l'étape 3.4, puis vérifie les Secrets Streamlit (dans l'app : ⋮ → **Settings** → **Secrets**) |
| « Sauvegarde impossible » en ajoutant un favori | le jeton n'a pas le droit d'écriture | recrée-le avec **Contents : Read and write** (étape 5a) |
| Pas d'e-mail | adresse d'expéditeur non vérifiée ou blocage IP dans Brevo | refais les 2 réglages Brevo de l'étape 2 ; le détail de l'erreur est dans **Actions** → **Recherche quotidienne** → le dernier passage |
| Peu d'offres | une clé est manquante ou mal nommée | compare tes noms de secrets avec le tableau de l'étape 3 (majuscules comprises) |
| Croix rouge ✗ sur **Tests** | faute de frappe dans `recherche_offres.py` | ouvre le passage en rouge : le message indique la ligne fautive |

## Bon à savoir

- **Récupérer les nouveautés** du projet partagé : sur la page de ta copie, bouton
  **Sync fork** → **Update branch**. Si GitHub signale un conflit (vous avez modifié
  les mêmes lignes, souvent tes réglages), garde ta version de tes réglages.
- **Proposer une amélioration** au projet partagé : fais-la dans ta copie, puis
  **Contribute** → **Open pull request**. C'est une demande : l'auteur l'accepte ou
  non. Tu ne peux pas modifier le projet partagé directement.
- **Ta copie dépend du projet partagé** : si ton accès est retiré ou si le projet est
  supprimé, GitHub supprime aussi ta copie. Pour une copie totalement indépendante,
  demande à l'auteur.
- **Coût** : gratuit. Un dépôt privé dispose de 2 000 min de GitHub Actions par
  mois ; ce projet en consomme au plus environ 1 300.
- **Tes réglages dans l'app** (filtres, seuils) ne sont pas sauvegardés : pour les
  garder, modifie `recherche_offres.py` (étape 4).
- **Lancer l'app sur ton ordinateur** (facultatif, pour les curieux) : installe
  Python 3.13, puis dans le dossier du projet `pip install -r requirements.txt`, crée
  un fichier `.env` avec les mêmes lignes que les Secrets Streamlit (sans guillemets,
  ex. `FT_CLIENT_ID=...`), et lance `streamlit run app.py`.
