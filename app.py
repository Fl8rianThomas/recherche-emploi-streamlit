#!/usr/bin/env python3
"""Interface web pour lire les offres d'emploi (France Travail + Adzuna).

Lancement :  streamlit run app.py
"""
import io
import re
import html
import math
import importlib
import csv
import os
import json
import base64
from collections import Counter
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from dotenv import load_dotenv

import recherche_offres as ro
import sites_carrieres as sc

# Après un push, Streamlit Cloud ré-exécute le NOUVEL app.py mais peut garder les
# ANCIENS modules en mémoire -> AttributeError (vu le 2026-10-13 : « no attribute
# GRANDS_GROUPES_PAR_DEFAUT » pendant ~50 min). On les recharge dès qu'un des deux
# fichiers a changé (date de modification) : le cœur d'abord, puis sites_carrieres
# qui en importe des fonctions.
_mtimes = (os.path.getmtime(ro.__file__), os.path.getmtime(sc.__file__))
if getattr(sc, "_mtimes_charges", None) != _mtimes:
    ro = importlib.reload(ro)
    sc = importlib.reload(sc)
    sc._mtimes_charges = _mtimes

# Instantané publié toutes les 4 h par GitHub Actions (branche `data`). L'app le
# lit à l'ouverture → données rafraîchies en arrière-plan, sans interroger les API.
# - Dépôt PUBLIC : URL brute (raw) directe, sans jeton.
# - Dépôt PRIVÉ : API GitHub authentifiée (jeton GH_TOKEN dans les Secrets), via le
#   media type « raw » qui gère les fichiers > 1 Mo.
SNAPSHOT_URL = ("https://raw.githubusercontent.com/dylanhatte/"
                "recherche-emploi/data/offres_snapshot.json")
SNAPSHOT_API_URL = ("https://api.github.com/repos/dylanhatte/recherche-emploi/"
                    "contents/offres_snapshot.json?ref=data")

# Offres sauvegardées (★) : petit fichier favoris.json sur une branche DÉDIÉE
# `favoris` (créée au besoin), lu ET écrit via l'API GitHub. Surtout PAS `data` :
# le snapshot la force-push toutes les 4 h et effaçait les sauvegardes. Nécessite
# un jeton GH_TOKEN avec le droit « Contents : Read and Write ». Chaque offre est
# stockée EN ENTIER pour rester affichable après sa sortie du flux des snapshots.
REPO_API = "https://api.github.com/repos/dylanhatte/recherche-emploi"
FAVORIS_BRANCHE = "favoris"
FAVORIS_API_URL = f"{REPO_API}/contents/favoris.json?ref={FAVORIS_BRANCHE}"
FAVORIS_PUT_URL = f"{REPO_API}/contents/favoris.json"

st.set_page_config(page_title="Recherche d'offres", page_icon="🔎", layout="wide")
load_dotenv(override=True)

# Hébergement (Streamlit Community Cloud, etc.) : il n'y a pas de fichier .env, les
# clés sont fournies via les « Secrets » Streamlit. On les recopie dans l'environnement
# pour que les os.getenv(...) du reste du code marchent sans modification. En local,
# .env a déjà tout chargé et ce bloc ne fait rien (try/except si aucun secret défini).
try:
    for _cle, _val in st.secrets.items():
        if isinstance(_val, str):
            os.environ.setdefault(_cle, _val)
except Exception:  # noqa: BLE001 — aucun secrets.toml configuré (cas local courant)
    pass


# === Recuperation par source (mise en cache par criteres) ===================
@st.cache_data(show_spinner=False, ttl=14400)  # 4 h : données rafraîchies au plus toutes les 4 h
def chercher_france_travail(mots_cles, departement, type_contrat, max_offres, jours_max):
    cid, secret = os.getenv("FT_CLIENT_ID"), os.getenv("FT_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "France Travail : FT_CLIENT_ID / FT_CLIENT_SECRET manquants dans .env"
        )
    token = ro.obtenir_token(cid, secret)
    config = dict(
        ro.CONFIG, mots_cles=mots_cles, departement=departement or None,
        type_contrat=type_contrat or None, max_offres=max_offres, jours_max=jours_max,
    )
    offres = ro.rechercher(token, config)
    for o in offres:
        o["_source"] = "France Travail"
    return offres


@st.cache_data(show_spinner=False, ttl=14400)  # 4 h : données rafraîchies au plus toutes les 4 h
def chercher_adzuna(mots_cles, lieu, type_contrat, max_offres, jours_max):
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError(
            "Adzuna : ADZUNA_APP_ID / ADZUNA_APP_KEY manquants dans .env "
            "(inscription gratuite sur developer.adzuna.com/signup)"
        )
    config = dict(
        ro.CONFIG, mots_cles=mots_cles, type_contrat=type_contrat or None,
        max_offres=max_offres, where_adzuna=lieu or None, jours_max=jours_max,
    )
    return ro.rechercher_adzuna(config, app_id, app_key)


@st.cache_data(show_spinner=False, ttl=14400)  # 4 h : données rafraîchies au plus toutes les 4 h
def chercher_jooble(mots_cles, lieu, max_offres):
    api_key = os.getenv("JOOBLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Jooble : JOOBLE_API_KEY manquant dans .env "
            "(clé gratuite sur jooble.org/api/about)"
        )
    config = dict(
        ro.CONFIG, mots_cles=mots_cles, max_offres=max_offres,
        location_jooble=lieu or None,
    )
    return ro.rechercher_jooble(config, api_key)


@st.cache_data(show_spinner=False, ttl=14400)  # 4 h : données rafraîchies au plus toutes les 4 h
def chercher_jobup(mots_cles, max_offres):
    """jobup.ch — job board suisse francophone, API publique sans clé."""
    config = dict(ro.CONFIG, mots_cles=mots_cles, max_offres=max_offres)
    return ro.rechercher_jobup(config)


@st.cache_data(show_spinner=False, ttl=14400)  # 4 h : données rafraîchies au plus toutes les 4 h
def chercher_sites_carrieres(mots_cles, max_offres, jours_max, inclure_titres,
                             exclure_titres):
    """Sites carrières des grands groupes (Workday, LVMH, BPCE), en parallèle.

    API publiques sans clé, offres France. -> (offres, erreurs par site).
    """
    config = dict(ro.CONFIG, max_offres=max_offres, jours_max=jours_max or None,
                  inclure_titres=list(inclure_titres), exclure_titres=list(exclure_titres))
    return sc.rechercher_sites_carrieres(config, [mots_cles])


@st.cache_data(show_spinner=False, ttl=600)  # relit l'instantané au plus toutes les 10 min
def charger_snapshot():
    """Lit l'instantané JSON publié toutes les 4 h (branche `data`).

    Si GH_TOKEN est défini (dépôt privé), lit via l'API GitHub authentifiée ;
    sinon via l'URL brute publique. Retourne (offres, genere_le, erreurs) ou
    (None, None, []) si indisponible. Aucune API métier n'est appelée ici.
    """
    token = os.getenv("GH_TOKEN")
    try:
        if token:
            r = requests.get(
                SNAPSHOT_API_URL, timeout=25,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github.raw",
                         "X-GitHub-Api-Version": "2022-11-28"})
        else:
            r = requests.get(SNAPSHOT_URL, timeout=25)
        if r.status_code != 200:
            return None, None, []
        d = r.json()
        return d.get("offres") or [], d.get("genere_le"), d.get("erreurs") or []
    except Exception:  # noqa: BLE001
        return None, None, []


@st.cache_data(show_spinner=False, ttl=120)
def charger_favoris():
    """Lit favoris.json (branche `favoris`) → (liste d'offres, sha).

    Renvoie ([], None) si le fichier n'existe pas encore (404) ou si GH_TOKEN est
    absent. Le `sha` est requis pour une écriture ultérieure (API Contents GitHub).
    """
    token = os.getenv("GH_TOKEN")
    if not token:
        return [], None
    try:
        r = requests.get(
            FAVORIS_API_URL, timeout=20,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        if r.status_code != 200:          # 404 = pas encore créé
            return [], None
        meta = r.json()
        contenu = base64.b64decode(meta.get("content", "")).decode("utf-8")
        d = json.loads(contenu) if contenu.strip() else {}
        return d.get("offres") or [], meta.get("sha")
    except Exception:  # noqa: BLE001
        return [], None


def _assurer_branche_favoris(entetes):
    """Crée la branche `favoris` (depuis la branche par défaut) si elle n'existe pas."""
    r = requests.get(f"{REPO_API}/git/refs/heads/{FAVORIS_BRANCHE}", headers=entetes,
                     timeout=20)
    if r.status_code == 200:
        return True
    base = requests.get(REPO_API, headers=entetes, timeout=20).json().get(
        "default_branch", "main")
    rb = requests.get(f"{REPO_API}/git/refs/heads/{base}", headers=entetes, timeout=20)
    if rb.status_code != 200:
        return False
    cr = requests.post(f"{REPO_API}/git/refs", headers=entetes, timeout=20,
                       json={"ref": f"refs/heads/{FAVORIS_BRANCHE}",
                             "sha": rb.json()["object"]["sha"]})
    return cr.status_code in (200, 201)


def _ecrire_favoris(offres, sha):
    """Écrit favoris.json sur la branche `favoris` via l'API GitHub. → (ok, err, sha)."""
    token = os.getenv("GH_TOKEN")
    if not token:
        return False, "Jeton GH_TOKEN absent — impossible de sauvegarder.", sha
    payload = {"maj_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "offres": offres}
    contenu = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    body = {"message": "maj favoris", "content": contenu, "branch": FAVORIS_BRANCHE}
    if sha:
        body["sha"] = sha
    entetes = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        if not sha and not _assurer_branche_favoris(entetes):   # 1re sauvegarde
            return False, f"Branche « {FAVORIS_BRANCHE} » impossible à créer.", sha
        r = requests.put(FAVORIS_PUT_URL, timeout=25, json=body, headers=entetes)
        if r.status_code in (200, 201):
            return True, None, (r.json().get("content") or {}).get("sha")
        try:
            msg = r.json().get("message", "")
        except Exception:  # noqa: BLE001
            msg = r.text[:160]
        return False, f"HTTP {r.status_code} — {msg[:160]}", sha
    except Exception as e:  # noqa: BLE001
        return False, str(e), sha


def _cle_favori(o):
    """Identifiant stable d'une offre : son URL (sinon repli sur le titre)."""
    return (champ(o, "origineOffre", "urlOrigine") or o.get("intitule", "")).strip()


def _set_favoris():
    """Ensemble des clés (URL) actuellement sauvegardées, pour test d'appartenance."""
    return {_cle_favori(o) for o in st.session_state.get("favoris", [])}


def basculer_favori(o):
    """Ajoute/retire l'offre des sauvegardées, persiste sur GitHub, puis relance."""
    cle = _cle_favori(o)
    actuels = st.session_state.get("favoris", [])
    favs = [x for x in actuels if _cle_favori(x) != cle]
    ajoute = len(favs) == len(actuels)      # rien retiré → c'est un ajout
    if ajoute:
        favs.append(o)
    ok, err, sha = _ecrire_favoris(favs, st.session_state.get("favoris_sha"))
    if ok:
        st.session_state["favoris"] = favs
        st.session_state["favoris_sha"] = sha
        charger_favoris.clear()             # invalide le cache pour les autres sessions
        # Toast affiché APRÈS le rerun (un toast émis juste avant st.rerun est perdu).
        st.session_state["_fav_msg"] = ("⭐ Offre sauvegardée" if ajoute
                                        else "Retirée des sauvegardées")
    else:
        st.session_state["_fav_err"] = err
    st.rerun()


def _fmt_dt_paris(iso):
    """Formate un horodatage ISO (UTC) en heure de Paris lisible (ex. 27/06 18h00)."""
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Paris"))
        return dt.strftime("%d/%m %Hh%M")
    except Exception:  # noqa: BLE001
        return iso


def _dt_paris_offre(iso):
    """Parse la date d'une offre (ISO, sources variées) -> datetime heure de Paris.

    Gère le « Z », les décalages, l'absence de fuseau (supposé UTC) et les
    fractions de seconde « .NET » à 7 chiffres (Jooble). Renvoie None si illisible.
    """
    if not iso:
        return None
    s = str(iso).strip().replace("Z", "+00:00")
    # Tronque une fraction de seconde > 6 chiffres (sinon fromisoformat échoue).
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:                 # horodatage sans fuseau -> supposé UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Europe/Paris"))


def fmt_date_heure(iso, court=False):
    """Date + heure de parution d'une offre, en heure de Paris.

    - « 27/06 à 14h30 » si une heure réelle est fournie (court=True -> « 27/06 14h30 ») ;
    - « 27/06 » si la source ne donne qu'une date (ou heure d'origine à minuit) ;
    - '' si la valeur est vide.
    """
    if not iso:
        return ""
    s = str(iso).strip()
    heure_brute = s.split("T", 1)[1][:5] if "T" in s else ""   # « HH:MM » d'origine
    a_heure = bool(heure_brute) and heure_brute != "00:00"
    dt = _dt_paris_offre(s)
    if dt is None:
        return s[:10]
    if not a_heure:
        return dt.strftime("%d/%m")
    return dt.strftime("%d/%m %Hh%M") if court else dt.strftime("%d/%m à %Hh%M")


def champ(o, *chemin, defaut=""):
    cur = o
    for cle in chemin:
        if not isinstance(cur, dict):
            return defaut
        cur = cur.get(cle, {})
    return cur if cur not in ({}, None) else defaut


def csv_bytes(lignes, champs):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=champs)
    w.writeheader()
    w.writerows(lignes)
    return buf.getvalue().encode("utf-8-sig")


# === En-tete : critères (en haut de page) ==================================
# Compacité : réduit le padding haut/bas et l'espace entre les blocs/expanders.
st.markdown(
    "<style>"
    ".block-container{padding-top:2.2rem;padding-bottom:1.5rem;}"
    "div[data-testid='stExpander']{margin-bottom:.35rem;}"
    "div[data-testid='stVerticalBlock']{gap:.6rem;}"
    "</style>",
    unsafe_allow_html=True,
)
st.title("🔎 Recherche d'offres")
st.caption("Recherche multi-variantes, filtrée des ESN, cabinets et doublons.")

TOUTES_SOURCES = ["France Travail", "Adzuna", "Jooble", "🏢 Sites carrières", "🚩 jobup.ch"]
contrats = ["", "CDI", "CDD", "MIS"]

# Couleurs de ligne : tons medium-fonces saturés (texte blanc cote CSS) pour
# bien se distinguer sur fond sombre. Coloriage par metier via « mot:couleur ».
COULEURS_LIGNE = {
    "violet": "#8e5bd0", "bleu": "#3b7dd8", "blue": "#3b7dd8",
    "vert": "#2fa564", "green": "#2fa564", "orange": "#dd7a1c",
    "rouge": "#d24545", "red": "#d24545", "jaune": "#c29a1c", "yellow": "#c29a1c",
    "rose": "#cc4a96", "pink": "#cc4a96", "cyan": "#1f9595",
    "gris": "#6f6f6f", "gray": "#6f6f6f",
}

# 1) Recherche (métiers, lieu, sources, contrat) — repliée une fois lancée
with st.expander("🔎 Critères de recherche", expanded=False):
    col_m, col_p = st.columns([3, 2])
    with col_m:
        variantes_txt = st.text_area(
            "Métiers recherchés (une variante par ligne)",
            value="\n".join(ro.SYNONYMES_PAR_DEFAUT), height=178,
            help="Chaque ligne lance une recherche distincte ; les résultats sont "
                 "fusionnés et dédoublonnés (ex: « analyste d'affaires »).",
        )
    with col_p:
        sources = st.multiselect(
            "Sources", TOUTES_SOURCES, default=TOUTES_SOURCES,
            help="Les sources cochées sont fusionnées et dédoublonnées.",
        )
        sc_lieu, sc_ct = st.columns(2)
        lieu_saisi = sc_lieu.text_input(
            "Lieu", value="",
            help="Code département (75, 69…) ou ville/région (Paris, Lyon…). "
                 "Vide = toute la France.",
        )
        type_contrat = sc_ct.selectbox(
            "Type de contrat", options=contrats,
            index=contrats.index(ro.CONFIG["type_contrat"] or ""),
            format_func=lambda x: x or "Tous",
        )
    variantes = [v.strip() for v in variantes_txt.splitlines() if v.strip()]
    departement, lieu_nom = ro.resoudre_lieu(lieu_saisi)
    if lieu_saisi.strip():
        det = []
        if departement:
            det.append(f"FT → dépt {departement}")
        if lieu_nom:
            det.append(f"Adzuna/Jooble → « {lieu_nom} »")
        st.caption(" · ".join(det) if det else "lieu non reconnu")

# 2) Filtres (période, volume, pertinence, exclusions)
with st.expander("🧹 Filtres — période, pertinence, exclusions"):
    fp1, fp2, fp3 = st.columns(3)
    jours_recent = int(fp1.number_input(
        "🆕 Offres récentes (jours)", min_value=0, max_value=60, value=1, step=1,
        help="Seuil de nouveauté : une offre ≤ N jours est marquée 🆕 et remontée "
             "en tête. N'exclut rien.",
    ))
    jours_max = int(fp2.number_input(
        "Ancienneté max (jours)", min_value=0, max_value=365, value=14, step=1,
        help="Exclut les offres parues il y a plus de N jours (0 = sans limite). "
             "Envoyé aux API → relance « Rechercher » pour élargir.",
    ))
    max_offres = fp3.slider(
        "Max d'offres / variante / source", 10, 200, 200, 10,
        help="Plafond par recherche. Le total cumulé peut être bien plus élevé.",
    )
    filtre_pertinence = st.checkbox(
        "Titre pertinent obligatoire", value=True,
        help="Ne garde que les offres dont le TITRE contient un des mots ci-dessous "
             "(élimine le bruit capté via la description seule).",
    )
    col_inc, col_exc = st.columns(2)
    with col_inc:
        if filtre_pertinence:
            inclusions_txt = st.text_area(
                "✅ Le titre doit contenir un de ces mots",
                value="\n".join(ro.INCLUSIONS_TITRE_PAR_DEFAUT), height=140,
                help="Un mot par ligne. Optionnel : « mot:couleur » colore la ligne "
                     "entière des offres dont le titre contient ce mot (couleurs : "
                     "violet, bleu, vert, orange, rouge, jaune, rose, cyan, gris). "
                     "La 1re couleur trouvée dans le titre l'emporte.",
            )
            inclure_titres, couleurs_mot = [], {}
            for ligne_inc in inclusions_txt.splitlines():
                ligne_inc = ligne_inc.strip()
                if not ligne_inc:
                    continue
                mot, sep, coul = ligne_inc.rpartition(":")
                coul = coul.strip().lower()
                if sep and mot.strip() and coul in COULEURS_LIGNE:
                    inclure_titres.append(mot.strip())
                    couleurs_mot[mot.strip()] = coul
                else:
                    inclure_titres.append(ligne_inc)
        else:
            inclure_titres, couleurs_mot = None, {}
            st.caption("Filtre titre désactivé : toutes les offres des sources sont gardées.")
    with col_exc:
        exclusions_txt = st.text_area(
            "🚫 Exclure si le titre contient un de ces mots",
            value="\n".join(ro.EXCLUSIONS_TITRE_PAR_DEFAUT), height=140,
            help="Offre écartée si son titre contient un de ces mots "
                 "(accents/casse ignorés). « mot sauf a, b » : exception si le "
                 "titre contient aussi a ou b (ex. « data analyst sauf business » "
                 "garde « Business Data Analyst »).",
        )
        exclure_titres = [x.strip() for x in exclusions_txt.splitlines() if x.strip()]
    groupes_txt = st.text_area(
        "🏢 Grands groupes (onglet dédié) — un nom par ligne",
        value="\n".join(ro.GRANDS_GROUPES_PAR_DEFAUT), height=140,
        help="Une offre France dont l'entreprise contient un de ces noms (mot entier, "
             "accents/casse ignorés) apparaît dans l'onglet 🏢 Grands groupes. "
             "« ^Nom » = le nom d'entreprise doit COMMENCER par Nom (évite « Sup de "
             "Vinci », « Nihon Kohden Orange »). ~40 groupes (Airbus, LVMH, BPCE…) "
             "sont en plus interrogés directement sur leur site carrière "
             "(source 🏢 Sites carrières).",
    )
    lib_groupes, rx_groupes = ro.regex_groupes(groupes_txt.splitlines())

# 3) Profil Optimum (classement par adéquation)
with st.expander("⭐ Profil Optimum — classement par adéquation"):
    profil_txt = st.text_area(
        "Compétences (une par ligne ; « terme:poids », ex. mutuelle:3)",
        value="\n".join(ro.PROFIL_OPTIMUM), height=140,
        help="Classe l'onglet France par adéquation. Les domaines (RH, mutuelle, "
             "assurance, banque) sont pondérés plus fort. Titre compté ×2.",
    )
    profil_optimum = [x.strip() for x in profil_txt.splitlines() if x.strip()]
    depts_txt = st.text_input(
        "🎯 Départements favoris (codes séparés par une virgule)",
        value=", ".join(ro.DEPTS_FAVORIS_PAR_DEFAUT),
        help="Une offre située dans un de ces départements reçoit l'icône 🎯 dans la "
             "liste (n'exclut rien). Défaut : 37 (Tours), 41 (Blois), 45 (Orléans), "
             "75 (Paris).",
    )
    depts_favoris = {d.strip().upper() for d in depts_txt.replace(";", ",").split(",") if d.strip()}

col_go, col_actu = st.columns(2)
go = col_go.button("🔎 Rechercher", type="primary", width="stretch",
                   help="Recherche EN DIRECT avec les critères ci-dessus.")
actu = col_actu.button("🔄 Actualiser", width="stretch",
                       help="Force une récupération EN DIRECT maintenant.")
st.caption("À l'ouverture, les offres viennent d'un **instantané rafraîchi toutes "
           "les 4 h** (aucune API appelée). **🔄 Actualiser** force du frais "
           "immédiatement ; **🔎 Rechercher** applique tes critères en direct.")
st.divider()

def _tag(offres, terme):
    """Marque chaque offre avec la variante qui l'a ramenée (badge « via »)."""
    for o in offres:
        o.setdefault("_variante", terme)
    return offres

# Deux modes d'alimentation :
#  - PAR DÉFAUT (ouverture du site) : on lit l'INSTANTANÉ publié toutes les 4 h
#    (aucune API appelée → pas de surconsommation de quota à la visite).
#  - clic « Rechercher » / « Actualiser » : recherche EN DIRECT, mémorisée en
#    session (st.session_state["live"]) pour le reste de la visite.
if go or actu:
    if not sources:
        st.warning("Sélectionne au moins une source.")
        st.stop()
    if not variantes:
        st.warning("Indique au moins un métier à rechercher.")
        st.stop()

    # On vide le cache de 4 h pour réinterroger réellement les API (frais immédiat).
    st.cache_data.clear()

    # Sources : (label, fn(terme)). jobup.ch (suisse francophone) alimente
    # l'onglet 🇨🇭 ; ses offres passent les mêmes filtres que la France.
    fetchers = []
    if "France Travail" in sources:
        fetchers.append(("France Travail", lambda t: chercher_france_travail(
            t, departement or "", type_contrat, max_offres, jours_max)))
    if "Adzuna" in sources:
        fetchers.append(("Adzuna", lambda t: chercher_adzuna(
            t, lieu_nom or "", type_contrat, max_offres, jours_max)))
    if "Jooble" in sources:
        fetchers.append(("Jooble", lambda t: chercher_jooble(t, lieu_nom or "", max_offres)))
    if "🏢 Sites carrières" in sources:
        # ~30 sites interrogés en parallèle ; renvoie (offres, erreurs par site).
        fetchers.append(("🏢 Sites carrières", lambda t: chercher_sites_carrieres(
            t, max_offres, jours_max, tuple(inclure_titres or ()), tuple(exclure_titres))))
    if "🚩 jobup.ch" in sources:
        fetchers.append(("🚩 jobup.ch", lambda t: chercher_jobup(t, max_offres)))

    brutes, erreurs, total = [], [], len(variantes) * len(fetchers)
    with st.status(f"Recherche : {len(variantes)} variantes × {len(fetchers)} sources "
                   f"({total} requêtes)…", expanded=True) as statut:
        barre = st.progress(0.0)
        journal = st.empty()
        lignes, fait = [], 0
        for terme in variantes:
            for nom_src, fn in fetchers:
                fait += 1
                barre.progress(fait / total, text=f"({fait}/{total}) {nom_src} — « {terme} »")
                try:
                    res = fn(terme)
                    if isinstance(res, tuple):        # (offres, erreurs) : sites carrières
                        res, errs_src = res
                        erreurs += errs_src
                    res = _tag(res, terme)
                    brutes += res
                    lignes.append(f"✅ **{nom_src}** · « {terme} » → {len(res)} offres "
                                  f"_(cumul brut : {len(brutes)})_")
                except Exception as e:  # noqa: BLE001
                    erreurs.append(f"{nom_src} « {terme} » : {e}")
                    lignes.append(f"⚠️ **{nom_src}** · « {terme} » → erreur : {str(e)[:80]}")
            journal.markdown("\n".join(f"- {x}" for x in lignes))
        barre.empty()
        statut.update(
            label=f"Recherche terminée — {len(brutes)} offres brutes récupérées",
            state="complete", expanded=False,
        )
    st.session_state["brutes"] = brutes
    st.session_state["erreurs"] = erreurs
    st.session_state["live"] = True
    # Re-rendu propre, sans le bloc de progression, pour figer la mise en page :
    # évite le "recadrement" au premier clic sur une offre.
    st.rerun()

if st.session_state.get("live"):
    # Données EN DIRECT (clic Rechercher/Actualiser cette session).
    brutes = st.session_state["brutes"]
    erreurs_aff = st.session_state.get("erreurs", [])
    st.caption("🟢 Données **en direct** (recherche manuelle). Recharge la page pour "
               "revenir à l'instantané automatique.")
else:
    # PAR DÉFAUT : instantané rafraîchi toutes les 4 h (aucune API appelée ici).
    snap_offres, genere_le, erreurs_aff = charger_snapshot()
    if snap_offres is None:
        st.warning("Instantané indisponible pour l'instant (il sera généré au "
                   "prochain cycle de 4 h). Clique **🔄 Actualiser** pour une "
                   "recherche en direct maintenant.")
        st.stop()
    brutes = snap_offres
    st.caption(f"🗂️ Instantané du **{_fmt_dt_paris(genere_le)}** "
               f"(rafraîchi automatiquement toutes les 4 h).")

if erreurs_aff:
    with st.expander(f"⚠️ {len(erreurs_aff)} avertissement(s) de récupération"):
        for msg in erreurs_aff:
            st.caption(msg)
if not brutes:
    st.error("Aucune offre disponible.")
    st.stop()

# Filtrage (ré-appliqué à chaque rerun : les filtres se modifient sans relancer
# la recherche, et le clic sur une annonce conserve les résultats)
gardees, exclues, stats = ro.filtrer(brutes, jours_max, exclure_titres, inclure_titres)

if not gardees:
    st.warning("Aucune offre retenue avec ces critères.")
    st.stop()

# Offres Suisse (source jobup.ch — toute suisse — ou ville suisse reconnue
# ailleurs), en EXCLUANT les offres rédigées en allemand : on ne garde que la
# Suisse francophone + les annonces en anglais. Mêmes filtres que France.
suisse = [o for o in gardees
          if (o.get("_source") == "jobup.ch" or ro.est_suisse(o))
          and not ro.est_allemand(o)]
# France : on exclut aussi les offres suisses (ex: « Analyste métier - Genève »
# postée sur France Travail) — elles vivent dans l'onglet 🇨🇭.
francaises = [o for o in gardees
              if o.get("_source") != "jobup.ch"
              and not ro.est_international(o) and not ro.est_suisse(o)]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Récupérées", stats["brutes"])
c2.metric("France", len(francaises))
c3.metric("🚩 Suisse", len(suisse))
c4.metric("Exclues", stats["exclues"])
c5.metric("Doublons", stats["doublons"])

# Récapitulatif visible de tout ce qui est filtré (y compris les listes du code)
with st.expander("🔧 Filtres actifs — rappel de ce qui est recherché et exclu"):
    col_g, col_d = st.columns(2)
    with col_g:
        st.markdown("**Recherche**")
        st.markdown(f"- **Sources** : {', '.join(sources)}")
        st.markdown(f"- **Métiers** : {', '.join(variantes)}")
        st.markdown(f"- **Lieu** : {lieu_saisi.strip() or 'toute la France'}")
        st.markdown(f"- **Contrat** : {type_contrat or 'tous'}")
        st.markdown(f"- **Ancienneté** : {('≤ ' + str(jours_max) + ' jours') if jours_max else 'illimitée'}")
        st.markdown("**Filtres sur le titre**")
        if inclure_titres:
            st.markdown(f"- ✅ Doit contenir : {', '.join(inclure_titres)}")
        else:
            st.markdown("- Pertinence par titre : *désactivée*")
        st.markdown(f"- 🚫 Exclu si contient : {', '.join(exclure_titres) or '—'}")
    with col_d:
        st.markdown(f"**🚫 ESN / sociétés de conseil exclues par nom** ({len(ro.ESN_CONNUES)})")
        st.caption(", ".join(sorted(ro.ESN_CONNUES)))
        st.markdown("**🚫 Signaux « régie / prestation » dans le texte**")
        st.caption(", ".join(ro.SIGNAUX_ESN))
        if ro.EXCLURE_AGENCES:
            st.markdown(f"**🚫 Cabinets de recrutement exclus** ({len(ro.AGENCES_RECRUTEMENT)})")
            st.caption(", ".join(sorted(ro.AGENCES_RECRUTEMENT)))
        else:
            st.markdown(f"**✅ Cabinets de recrutement conservés (prioritaires)** ({len(ro.AGENCES_RECRUTEMENT)})")
            st.caption("Une offre d'un de ces cabinets est gardée même si elle "
                       "matche un filtre ESN (nom) ou un signal régie/prestation : "
                       + ", ".join(sorted(ro.AGENCES_RECRUTEMENT)))
    st.caption("Les ESN et signaux sont définis dans le code (recherche_offres.py). "
               "Demande-moi d'en ajouter/retirer à tout moment.")

champs = ["intitule", "entreprise", "lieu", "contrat", "date", "url"]
SEUIL_RECENT = (date.today() - timedelta(days=jours_recent)).isoformat()


def _est_recente(o):
    d = (o.get("dateCreation", "") or "")[:10]
    return bool(d) and d >= SEUIL_RECENT


AUJOURDHUI = date.today().isoformat()
HIER = (date.today() - timedelta(days=1)).isoformat()
# Lundi 00h00 de la semaine en cours (weekday(): lundi=0 … dimanche=6).
LUNDI = (date.today() - timedelta(days=date.today().weekday())).isoformat()

# Détection « profil Business Analyst » d'après le titre (même logique que la légende).
BA_TERMES = ("analyst", "business", "amoa", "fonctionnel")


def _est_ba(o):
    titre = ro.normaliser(o.get("intitule", ""))
    return any(t in titre for t in BA_TERMES)


def _bucket_jour(it):
    """Libellé du groupe temporel d'une offre (Aujourd'hui / Hier / date)."""
    d = (it["o"].get("dateCreation", "") or "")[:10]
    if d == AUJOURDHUI:
        return "📅 Aujourd'hui"
    if d == HIER:
        return "📅 Hier"
    return f"📅 {d}" if d else "📅 Date inconnue"


def _rang_jour4(it):
    """Rang du groupe temporel (0 Aujourd'hui … 3 Plus anciennes)."""
    d = (it["o"].get("dateCreation", "") or "")[:10]
    if d == AUJOURDHUI:
        return 0
    if d == HIER:
        return 1
    if d and d >= LUNDI:
        return 2
    return 3


_LIBELLES_JOUR4 = {
    0: "📅 Aujourd'hui", 1: "📅 Hier",
    2: "🗓️ Cette semaine", 3: "🕒 Offres plus anciennes",
}


def _bucket_jour4(it):
    """Libellé en 4 tranches : Aujourd'hui / Hier / Cette semaine / Plus anciennes."""
    return _LIBELLES_JOUR4[_rang_jour4(it)]


def afficher_detail(it, cle=""):
    """Panneau de droite : détail complet de l'offre sélectionnée."""
    o = it["o"]
    titre = o.get("intitule", "Sans titre")
    ent = ro.nom_entreprise(o) or "Entreprise non précisée"
    lieu = champ(o, "lieuTravail", "libelle", defaut="-")
    contrat = o.get("typeContratLibelle") or o.get("typeContrat", "")
    d = fmt_date_heure(o.get("dateCreation"))
    salaire = champ(o, "salaire", "libelle")
    url = champ(o, "origineOffre", "urlOrigine")
    src, via = o.get("_source", ""), o.get("_variante", "")
    desc = (o.get("description", "") or "").strip()

    st.markdown(f"## {titre}")
    meta = f"**{ent}** · {lieu} · {contrat}"
    if d:
        meta += f" · 📅 {d}"
    if salaire:
        meta += f" · 💶 {salaire}"
    st.markdown(meta)
    if it.get("favori"):
        st.markdown("🎯 **Département favori**")
    if it.get("ba"):
        st.markdown("🔵 :blue[**Profil Business Analyst / AMOA**]")
    if it.get("po"):
        st.markdown("🟣 :violet[**Profil Product Owner / Product Manager**]")
    if it.get("rh"):
        rh_mots = ro.mots_rh_offre(o)
        chips = " ".join(f"`{m}`" for m in rh_mots)
        st.markdown(f"👥 **Domaine RH détecté** : {chips}")
    if it.get("comp"):
        chips = " ".join(f"`{m}`" for m in it["comp"])
        st.markdown(f"⭐ **Score {it['score']}** ({len(it['comp'])} compétences) : {chips}")
    badges = " ".join(f"`{x}`" for x in (src, f"via {via}" if via else "") if x)
    if badges:
        st.caption(badges)
    deja = _cle_favori(o) in _set_favoris()
    c_lien, c_fav = st.columns(2)
    if url:
        c_lien.link_button("➡️ Voir / postuler", url, type="primary",
                           width="stretch")
    if os.getenv("GH_TOKEN"):
        if c_fav.button("★ Retirer des sauvegardées" if deja else "☆ Sauvegarder",
                        key=f"fav_{cle}", width="stretch"):
            basculer_favori(o)
    st.divider()
    if desc:
        st.write(desc)
    else:
        st.caption("Pas de description fournie par cette source — ouvre l'offre via le lien.")


def _csv_items(items):
    has_score = bool(items) and "score" in items[0]
    rows = []
    for it in items:
        o = it["o"]
        row = ro.ligne(o)  # intitule, entreprise, lieu, contrat, date, url
        row["source"] = o.get("_source", "")
        row["salaire"] = champ(o, "salaire", "libelle")
        row["rh"] = ", ".join(ro.mots_rh_offre(o))
        if has_score:
            row["score"] = it["score"]
            row["competences"] = ", ".join(it.get("comp") or [])
        row["description"] = (o.get("description") or "").replace("\n", " ").strip()[:1500]
        rows.append(row)
    cols = (["intitule", "entreprise", "lieu", "source", "salaire", "contrat", "date", "rh"]
            + (["score", "competences"] if has_score else [])
            + ["url", "description"])
    return csv_bytes(rows, cols)


# Items de liste : texte aligné à gauche et limité à UNE seule ligne (… si trop long).
# NB : les deux panneaux ont une hauteur FIXE en pixels (scroll interne géré par
# Streamlit). Pas de hauteur en « 100vh » : la page défile naturellement quand on
# déplie un expander, sans bloquer l'accès au bas de page.
st.markdown(
    "<style>"
    "section.main div.stButton > button "
    "{ text-align:left; justify-content:flex-start; overflow:hidden; }"
    "section.main div.stButton > button p "
    "{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin:0; }"
    "</style>",
    unsafe_allow_html=True,
)


# Hauteur fixe (px) des deux panneaux liste/détail. Volontairement en pixels et
# non en « 100vh » : la page reste défilable quand on déplie un expander (un calc
# basé sur la fenêtre bloquait l'accès au bas de page). Augmenter pour voir plus
# d'offres et de description d'un coup d'œil.
HAUTEUR_PANNEAU = 820


def vue_liste_detail(items, cle, csv_nom, bucket_fn=None):
    """Affichage façon LinkedIn : liste cliquable à gauche, détail à droite.

    bucket_fn : fonction it -> libellé de tranche. Si fournie, un en-tête est
    inséré à chaque changement de tranche (ex. Aujourd'hui / Hier / Cette semaine
    / Plus anciennes). Sinon, séparateur 🆕 récentes / plus anciennes par défaut.
    """
    if not items:
        st.info("Aucune offre à afficher.")
        return
    st.download_button(
        "⬇️ Télécharger (CSV)", data=_csv_items(items),
        file_name=csv_nom, mime="text/csv", key=f"dl_{cle}",
    )
    sel_key = f"sel_{cle}"
    sel = min(st.session_state.get(sel_key, 0), len(items) - 1)
    favset = _set_favoris()
    # Coloriage du fond des lignes par métier (classe st-key-<cle>_<i> de Streamlit).
    regles = []
    for i, it in enumerate(items):
        bg = COULEURS_LIGNE.get(it.get("couleur"))
        if bg:
            sel_extra = "box-shadow:inset 5px 0 0 #fff !important;" if i == sel else ""
            regles.append(
                f".st-key-{cle}_{i} button {{ background-color:{bg} !important; "
                f"border:1px solid rgba(255,255,255,.25) !important; {sel_extra} }}"
                f".st-key-{cle}_{i} button p {{ color:#fff !important; }}")
    if regles:
        st.markdown("<style>" + "".join(regles) + "</style>", unsafe_allow_html=True)
    col_liste, col_detail = st.columns([2, 3], gap="medium")
    with col_liste:
        with st.container(height=HAUTEUR_PANNEAU, key=f"scroll_{cle}_l"):
            for i, it in enumerate(items):
                if bucket_fn is not None:
                    # En-tête de tranche temporelle, inséré à chaque changement
                    bucket = bucket_fn(it)
                    if i == 0 or bucket != bucket_fn(items[i - 1]):
                        if i > 0:
                            st.divider()
                        st.caption(bucket)
                # Séparateur entre les offres récentes (🆕) et les plus anciennes
                elif i > 0 and items[i - 1].get("recent") and not it.get("recent"):
                    st.divider()
                    st.caption("🕒 Offres plus anciennes")
                o = it["o"]
                titre = o.get("intitule", "Sans titre")
                ent = ro.nom_entreprise(o) or "Entreprise non précisée"
                d = fmt_date_heure(o.get("dateCreation"), court=True)
                lieu = champ(o, "lieuTravail", "libelle")
                bdg = ("★ " if _cle_favori(o) in favset else "")
                if it.get("recent"):
                    bdg += "🆕 "
                if it.get("favori"):
                    bdg += "🎯 "
                if it.get("rh"):
                    bdg += "👥 "
                if it.get("score"):
                    bdg += f"⭐{it['score']} "
                if lieu:
                    bdg += f"📍{lieu} "
                if it.get("km") is not None:                  # onglet 🗺️ : distance
                    bdg += f"🚗{it['km']:.0f} km "
                if it.get("esn"):                             # onglet 🗺️ : option ESN
                    bdg += "🏭 "
                if d:
                    bdg += f"📅{d} "
                label = f"{bdg}· {titre} — {ent}"
                if st.button(label, key=f"{cle}_{i}", width="stretch",
                             type="primary" if i == sel else "secondary"):
                    st.session_state[sel_key] = i
                    st.rerun()
    with col_detail:
        with st.container(height=HAUTEUR_PANNEAU, key=f"scroll_{cle}_d"):
            afficher_detail(items[sel], cle)


# Couleur de ligne d'une offre : 1re couleur (mot:couleur) trouvée dans le titre.
def _couleur_offre(o):
    if not couleurs_mot:
        return None
    titre = ro.normaliser(o.get("intitule", ""))
    for mot in (inclure_titres or []):
        c = couleurs_mot.get(mot)
        if c and ro.normaliser(mot) in titre:
            return c
    return None

# Décoration commune (France & Suisse) : score Optimum, RH, récence, puis tri
# 🆕 récentes d'abord, puis adéquation profil (⭐), puis date — décroissant.
def _decorer(offres):
    items = []
    for o in offres:
        sc, comp = ro.score_optimum(o, profil_optimum)
        items.append({"o": o, "score": sc, "comp": comp,
                      "recent": _est_recente(o), "rh": ro.comprend_rh(comp),
                      "po": ro.est_product_owner(o), "ba": _est_ba(o),
                      "couleur": _couleur_offre(o),
                      "favori": ro.est_departement_favori(o, depts_favoris)})
    items.sort(key=lambda it: (it["recent"], it["score"],
                               (it["o"].get("dateCreation", "") or "")[:10]), reverse=True)
    return items

def _legende(items):
    """Bandeau de comptage : total + 🆕/⭐/👥 + chips couleur BA/PO."""
    n = len(items)
    nrec = sum(1 for it in items if it.get("recent"))
    nsc = sum(1 for it in items if it.get("score"))
    nrh = sum(1 for it in items if it.get("rh"))
    npo = sum(1 for it in items if it.get("po"))
    nfav = sum(1 for it in items if it.get("favori"))
    nba = sum(1 for it in items if it.get("ba"))
    hex_ba = COULEURS_LIGNE.get(couleurs_mot.get("analyst") or couleurs_mot.get("business"), "#3b7dd8")
    hex_po = COULEURS_LIGNE.get(couleurs_mot.get("owner") or couleurs_mot.get("product manager"), "#dd7a1c")

    def pill(contenu, bg=None):
        style = ("padding:2px 9px;border-radius:11px;white-space:nowrap;"
                 "font-size:.85rem;" + (f"background:{bg};color:#fff;font-weight:600;"
                                        if bg else "background:rgba(128,128,128,.18);"))
        return f'<span style="{style}">{contenu}</span>'

    chips = [
        pill(f"📋 {n} offres"),
        pill(f"🎯 {nfav} favoris"),
        pill(f"🆕 {nrec} récentes"),
        pill(f"⭐ {nsc} scorées"),
        pill(f"👥 {nrh} RH/SIRH"),
        pill(f"BA&nbsp;{nba}", hex_ba),
        pill(f"PO&nbsp;{npo}", hex_po),
    ]
    return ('<div style="display:flex;flex-wrap:wrap;gap:7px;align-items:center;'
            'margin:.1rem 0 .55rem">' + "".join(chips) + '</div>')


def _filtrer_recherche(items, requete):
    """Filtre une liste d'items par mots-clés (titre + entreprise + lieu + description).

    Recherche AND : chaque mot doit apparaître quelque part (accents/casse ignorés).
    Requête vide -> liste inchangée.
    """
    mots = ro.normaliser(requete or "").split()
    if not mots:
        return items
    res = []
    for it in items:
        o = it["o"]
        txt = ro.normaliser(
            o.get("intitule", "") + " " + (ro.nom_entreprise(o) or "") + " "
            + champ(o, "lieuTravail", "libelle") + " " + (o.get("description") or ""))
        if all(mot in txt for mot in mots):
            res.append(it)
    return res


france_items = _decorer(francaises)
# Onglet France : 4 tranches temporelles (Aujourd'hui / Hier / Cette semaine /
# Plus anciennes) ; DANS chaque tranche, les 👥 RH/SIRH remontent en tête, puis
# le classement reprend par score ⭐ puis date. Tri stable en 2 passes : d'abord
# date décroissante, puis (tranche, RH, score) — l'ordre date est conservé à
# l'intérieur d'un même (tranche, RH, score).
france_items.sort(key=lambda it: (it["o"].get("dateCreation", "") or "")[:10],
                  reverse=True)
france_items.sort(key=lambda it: (_rang_jour4(it), 0 if it.get("rh") else 1,
                                  -it["score"]))
suisse_items = _decorer(suisse)

# Onglet « Perfect » : sous-ensemble France le plus exigeant — score > 2 ⭐ ET
# domaine RH/SIRH 👥 ET département favori 🎯. Tri strict par date (la plus
# récente d'abord), avec séparation Aujourd'hui / Hier / plus ancien.
perfect_items = sorted(
    [it for it in france_items
     if it["score"] > 2 and it.get("rh") and it.get("favori")],
    key=lambda it: (it["o"].get("dateCreation", "") or ""), reverse=True,
)

# Onglet « Grands groupes » : offres France publiées par un groupe de la liste
# éditable (banques, luxe, aéro…) — mêmes filtres et tranches que France. Dans
# chaque tranche : 👥 RH/SIRH d'abord, puis 🎯 départements favoris (classement
# seulement, aucune offre écartée), puis score ⭐. Tri stable -> date conservée.
for it in france_items:
    it["groupe"] = ro.groupe_offre(it["o"], lib_groupes, rx_groupes)
groupes_items = [it for it in france_items if it["groupe"]]
groupes_items.sort(key=lambda it: (_rang_jour4(it), 0 if it.get("rh") else 1,
                                   0 if it.get("favori") else 1, -it["score"]))

# Onglet « Autour de chez moi » : offres France géolocalisées (coordonnées de la
# source, sinon code postal / commune du libellé) dans un rayon autour du domicile.
# Domicile et rayon sont des widgets de l'onglet : lus ici via leur clé pour que le
# compteur du titre d'onglet soit à jour.
geo_domicile = st.session_state.get("geo_domicile", ro.DOMICILE)
geo_rayon = st.session_state.get("geo_rayon", ro.RAYON_DOMICILE_KM)
geo_centre = ro.coords_lieu(geo_domicile)
geo_base = france_items
if st.session_state.get("geo_esn"):
    # Option de l'onglet : autour de chez soi, le marché BA est surtout tenu par des
    # ESN (exclues partout ailleurs). Même filtrage SANS le test ESN, marquées 🏭.
    _g_esn, _, _ = ro.filtrer(brutes, jours_max, exclure_titres, inclure_titres,
                              ignorer_esn=True)
    geo_base = _decorer([o for o in _g_esn if o.get("_source") != "jobup.ch"
                         and not ro.est_international(o) and not ro.est_suisse(o)])
    for it in geo_base:
        it["esn"] = not ro.classer(it["o"])[0]
geo_items, geo_sans_lieu = [], 0
for it in geo_base:
    g = ro.geoloc_offre(it["o"])
    if not g:
        geo_sans_lieu += 1
    elif geo_centre and (km := ro.distance_km(geo_centre, g)) <= geo_rayon:
        geo_items.append({**it, "geo": g, "km": km})
geo_items.sort(key=lambda it: it["km"])

# Offres sauvegardées (★) — chargées une fois par session depuis la branche `favoris`,
# puis maintenues en session_state (la liste fait foi pendant toute la visite).
if "favoris" not in st.session_state:
    _favs, _sha = charger_favoris()
    st.session_state["favoris"] = _favs
    st.session_state["favoris_sha"] = _sha
favoris_items = _decorer(st.session_state["favoris"])

# Erreur de sauvegarde éventuelle (affichée en haut, visible quel que soit l'onglet).
if st.session_state.get("_fav_err"):
    st.error(f"⚠️ Sauvegarde impossible : {st.session_state.pop('_fav_err')}  \n"
             "Vérifie que le jeton **GH_TOKEN** a le droit « Contents : Read **and Write** ».")
if st.session_state.get("_fav_msg"):
    st.toast(st.session_state.pop("_fav_msg"))

(onglet_perfect, onglet_fr, onglet_gg, onglet_geo, onglet_ch, onglet_fav,
 onglet_excl, onglet_dash) = st.tabs([
    f"💎 Perfect ({len(perfect_items)})",
    f"🎯 France ({len(france_items)})",
    f"🏢 Grands groupes ({len(groupes_items)})",
    f"🗺️ Autour de chez moi ({len(geo_items)})",
    f"🚩 Suisse ({len(suisse_items)})",
    f"💾 Sauvegardées ({len(favoris_items)})",
    f"🗑️ Exclues ({len(exclues)})",
    "📊 Tableau de bord",
])
with onglet_perfect:
    st.markdown(_legende(perfect_items), unsafe_allow_html=True)
    st.caption("**Perfect** = offres France les plus alignées : score **> 2 ⭐** "
               "**ET** domaine **👥 RH/SIRH** **ET** 🎯 département favori. "
               "Triées par date (plus récente d'abord), séparées Aujourd'hui / Hier.")
    vue_liste_detail(perfect_items, "perfect", "offres_perfect.csv",
                     bucket_fn=_bucket_jour)

with onglet_fr:
    requete_fr = st.text_input(
        "🔎 Rechercher dans les offres France", key="recherche_fr",
        placeholder="ex. SIRH Paris, ou un nom d'entreprise…",
        help="Filtre la liste : titre, entreprise, lieu et description. Plusieurs "
             "mots = tous doivent apparaître (accents/casse ignorés).",
    )
    france_affiche = _filtrer_recherche(france_items, requete_fr)
    st.markdown(_legende(france_affiche), unsafe_allow_html=True)
    if requete_fr.strip():
        st.caption(f"🔎 **{len(france_affiche)}** résultat(s) sur {len(france_items)} "
                   f"pour « {requete_fr.strip()} ».")
    else:
        st.caption("Regroupé par **📅 Aujourd'hui / 📅 Hier / 🗓️ Cette semaine / 🕒 Plus "
                   "anciennes** ; dans chaque tranche, 👥 RH/SIRH en tête puis score ⭐. "
                   "Couleur de ligne = métier (configurable « mot:couleur » dans les Filtres).")
    vue_liste_detail(france_affiche, "fr", "offres_france.csv", bucket_fn=_bucket_jour4)

with onglet_gg:
    st.markdown(_legende(groupes_items), unsafe_allow_html=True)
    par_groupe = Counter(it["groupe"] for it in groupes_items)
    st.caption("Offres France publiées par une **banque ou un grand groupe** (liste "
               "éditable dans 🧹 Filtres), mêmes filtres que France. Dans chaque "
               "tranche : **👥 RH/SIRH** en tête, puis **🎯 départements favoris** "
               "(aucune offre écartée), puis score ⭐." + ("  \n" + " · ".join(f"**{g}** {n}"
                                                 for g, n in par_groupe.most_common())
                            if par_groupe else ""))
    if not groupes_items:
        st.info("Aucune offre de grand groupe avec ces critères.")
    else:
        vue_liste_detail(groupes_items, "gg", "offres_grands_groupes.csv",
                         bucket_fn=_bucket_jour4)

def _bucket_km(it):
    """Tranche de distance pour la liste de l'onglet 🗺️."""
    bas = 0
    for haut in (15, 30, 60, 100, 150, 200, 300):
        if it["km"] <= haut:
            return f"📍 {bas}–{haut} km"
        bas = haut
    return f"📍 plus de {bas} km"


def _rgb(hexa, defaut=(140, 140, 140)):
    """« #3b7dd8 » -> [59, 125, 216] (couleurs de ligne -> couleurs de carte)."""
    try:
        return [int(hexa[i:i + 2], 16) for i in (1, 3, 5)]
    except (TypeError, ValueError):
        return list(defaut)


def carte_offres(items, centre, rayon_km, domicile):
    """Carte pydeck : une bulle par lieu (taille = nombre d'offres, couleur = métier
    majoritaire), le domicile et le cercle du rayon. -> lieu sélectionné (lat, lon)
    ou None. Fond Carto (sans clé), infobulle au survol, clic = filtre de la liste."""
    lieux = {}
    for it in items:
        p = lieux.setdefault(it["geo"], {"lat": it["geo"][0], "lon": it["geo"][1],
                                         "km": round(it["km"]), "titres": [],
                                         "couleurs": Counter(), "ville": ""})
        p["titres"].append(f'{it["o"].get("intitule", "")[:60]} — '
                           f'{ro.nom_entreprise(it["o"])[:30]}')
        p["couleurs"][it.get("couleur")] += 1
        p["ville"] = p["ville"] or champ(it["o"], "lieuTravail", "libelle")[:40]
    points = []
    for p in lieux.values():
        n = len(p["titres"])
        points.append({
            "lat": p["lat"], "lon": p["lon"], "n": n, "n_txt": str(n), "km": p["km"],
            "ville": html.escape(p["ville"]),
            "titres": "<br/>".join(html.escape(t) for t in p["titres"][:6])
                      + (f"<br/>… +{n - 6}" if n > 6 else ""),
            "couleur": _rgb(COULEURS_LIGNE.get(p["couleurs"].most_common(1)[0][0])) + [215],
            "rayon_m": 1800 + 1400 * n ** 0.5,
        })
    maison = [{"lat": centre[0], "lon": centre[1], "ville": html.escape(f"🏠 {domicile}"),
               "titres": "Domicile", "km": 0, "n": 0}]
    couches = [
        pdk.Layer("ScatterplotLayer", data=maison, get_position="[lon, lat]",
                  get_radius=rayon_km * 1000, filled=False, stroked=True,
                  get_line_color=[231, 76, 60, 160], line_width_min_pixels=2),
        pdk.Layer("ScatterplotLayer", id="offres", data=points, get_position="[lon, lat]",
                  get_radius="rayon_m", radius_min_pixels=7, radius_max_pixels=38,
                  get_fill_color="couleur", stroked=True, get_line_color=[255, 255, 255],
                  line_width_min_pixels=1, pickable=True, auto_highlight=True),
        pdk.Layer("TextLayer", data=points, get_position="[lon, lat]", get_text="n_txt",
                  get_size=13, get_color=[255, 255, 255], font_weight=700),
        pdk.Layer("ScatterplotLayer", id="maison", data=maison, get_position="[lon, lat]",
                  get_radius=1200, radius_min_pixels=8, get_fill_color=[231, 76, 60],
                  stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2,
                  pickable=True),
    ]
    zoom = max(4.5, min(10.0, 8.3 - math.log2(max(rayon_km, 10) / 50)))
    deck = pdk.Deck(
        layers=couches,
        initial_view_state=pdk.ViewState(latitude=centre[0], longitude=centre[1], zoom=zoom),
        map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        tooltip={"html": "<b>{ville}</b> · {km} km<br/>{titres}",
                 "style": {"fontSize": "12px", "maxWidth": "420px"}},
    )
    evt = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object",
                          key="carte_geo", height=520)
    objets = ((getattr(evt, "selection", None) or {}).get("objects") or {}).get("offres") or []
    return (objets[0]["lat"], objets[0]["lon"]) if objets else None


with onglet_geo:
    c_dom, c_rayon, c_esn = st.columns([2, 3, 2])
    c_dom.text_input("🏠 Domicile (commune ou code postal)", value=ro.DOMICILE,
                     key="geo_domicile")
    c_rayon.slider("Rayon (km)", min_value=10, max_value=300, step=10,
                   value=ro.RAYON_DOMICILE_KM, key="geo_rayon")
    c_esn.checkbox("Inclure ESN / cabinets 🏭", key="geo_esn",
                   help="Autour de chez toi, beaucoup d'offres BA viennent d'ESN (exclues "
                        "partout ailleurs). Coche pour les voir ici, marquées 🏭.")
    if not geo_centre:
        st.warning(f"Lieu « {geo_domicile} » introuvable : indique une commune de plus "
                   "de 1 000 habitants ou un code postal.")
    else:
        st.markdown(_legende(geo_items), unsafe_allow_html=True)
        st.caption(f"**{len(geo_items)}** offres France à moins de **{geo_rayon} km** de "
                   f"{geo_domicile} (à vol d'oiseau) · {geo_sans_lieu} offres sans lieu "
                   "précis (« Île-de-France », « France ») ne sont pas placées. Survole une "
                   "bulle pour le détail ; **clique-la** pour filtrer la liste. En plus des "
                   f"recherches nationales, Adzuna est interrogé autour de {ro.DOMICILE} "
                   f"({ro.RAYON_DOMICILE_KM} km).")
        choix = carte_offres(geo_items, geo_centre, geo_rayon, geo_domicile)
        liste = [it for it in geo_items if it["geo"] == choix] if choix else geo_items
        if choix and liste:
            st.info(f"📍 Filtre : **{champ(liste[0]['o'], 'lieuTravail', 'libelle')}** "
                    f"({len(liste)} offre(s)) — clique à côté des bulles pour tout revoir.")
        if not geo_items:
            st.info("Aucune offre dans ce rayon : élargis-le avec le curseur.")
        else:
            vue_liste_detail(liste, "geo", "offres_autour_de_chez_moi.csv",
                             bucket_fn=_bucket_km)

with onglet_ch:
    st.markdown(_legende(suisse_items), unsafe_allow_html=True)
    st.caption("**Suisse** via jobup.ch + offres suisses des sources France — "
               "**allemand exclu** (romande + anglais). Mêmes filtres et scoring que "
               "France ; tri 🆕 récentes puis score ⭐.")
    vue_liste_detail(suisse_items, "ch", "offres_suisse.csv")

with onglet_fav:
    if not os.getenv("GH_TOKEN"):
        st.warning("Jeton **GH_TOKEN** non configuré : la sauvegarde est désactivée. "
                   "Ajoute-le dans les Secrets (droit « Contents : Read and Write »).")
    st.markdown(_legende(favoris_items), unsafe_allow_html=True)
    st.caption("Offres mises de côté via le bouton **☆ Sauvegarder** du détail. "
               "Stockées sur GitHub (branche `favoris`) → conservées entre les sessions "
               "et accessibles depuis tous tes appareils. ★ Retirer pour les enlever.")
    if not favoris_items:
        st.info("Aucune offre sauvegardée. Ouvre une offre puis clique **☆ Sauvegarder**.")
    else:
        vue_liste_detail(favoris_items, "fav", "offres_sauvegardees.csv")

with onglet_excl:
    # Tableau des offres exclues par le filtrage France, filtrable + triable.
    lignes_tbl = []
    for e in exclues:
        lignes_tbl.append({
            "intitule": e.get("intitule", ""), "entreprise": e.get("entreprise", ""),
            "lieu": e.get("lieu", ""), "contrat": e.get("contrat", ""),
            "date": e.get("date", ""), "url": e.get("url", ""),
            "statut": "Exclue", "motif": e.get("raison_exclusion", ""), "source": "",
        })

    if not lignes_tbl:
        st.info("Aucune offre exclue.")
    else:
        with st.expander("Répartition des exclusions par motif"):
            for raison, n in sorted(stats["compte"].items(), key=lambda x: -x[1]):
                st.markdown(f"- {raison} : **{n}**")

        f1, f2, f3 = st.columns([1, 2, 2])
        statuts = f1.multiselect("Statut", ["Exclue"],
                                 default=["Exclue"], key="f_statut")
        motifs_dispo = sorted({r["motif"] for r in lignes_tbl if r["motif"]})
        motifs = f2.multiselect("Motif d'exclusion", motifs_dispo, default=motifs_dispo,
                                key="f_motif")
        recherche = f3.text_input("Filtrer (titre / entreprise)", key="f_txt")

        rt = recherche.strip().lower()
        table = [
            r for r in lignes_tbl
            if r["statut"] in statuts
            and not (r["statut"] == "Exclue" and motifs_dispo and r["motif"] not in motifs)
            and (not rt or rt in (r["intitule"] + " " + r["entreprise"]).lower())
        ]
        st.caption(f"{len(table)} ligne(s) — clique un en-tête de colonne pour trier.")
        st.dataframe(
            table, width="stretch", hide_index=True,
            column_order=["statut", "motif", "intitule", "entreprise", "lieu",
                          "contrat", "date", "source", "url"],
            column_config={
                "url": st.column_config.LinkColumn("Lien", display_text="ouvrir"),
                "statut": "Statut", "motif": "Motif", "intitule": "Intitulé",
                "entreprise": "Entreprise", "lieu": "Lieu", "contrat": "Contrat",
                "date": "Date", "source": "Source",
            },
        )

with onglet_dash:
    if not francaises:
        st.info("Aucune offre France à analyser.")
    else:
        dates = [(o.get("dateCreation", "") or "")[:10] for o in francaises]
        dates = [d for d in dates if d]
        regions = [ro.region_offre(o) for o in francaises]
        par_region = Counter(regions)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Offres France", len(francaises))
        m2.metric(f"Récentes (≤ {jours_recent} j)", sum(1 for o in francaises if _est_recente(o)))
        m3.metric("Régions couvertes", sum(1 for r in par_region if r not in ("Autre", "Non précisé")))
        top = par_region.most_common(1)
        m4.metric("Région n°1", top[0][0] if top else "—")

        st.subheader("📈 Évolution du nombre d'offres (par date de parution)")
        if dates:
            par_date = Counter(dates)
            jours = sorted(par_date)
            df_dates = pd.DataFrame({"Offres": [par_date[j] for j in jours]}, index=jours)
            df_dates.index.name = "Date"
            st.bar_chart(df_dates, color="#2E86DE")
        else:
            st.caption("Pas de dates exploitables.")

        st.subheader("🗺️ Répartition par région")
        regions_tri = sorted(par_region, key=lambda r: par_region[r], reverse=True)
        df_reg = pd.DataFrame({"Offres": [par_region[r] for r in regions_tri]}, index=regions_tri)
        df_reg.index.name = "Région"
        st.bar_chart(df_reg, horizontal=True, color="#27AE60")
