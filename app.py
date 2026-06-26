#!/usr/bin/env python3
"""Interface web pour lire les offres d'emploi (France Travail + Adzuna).

Lancement :  streamlit run app.py
"""
import io
import csv
import os
from collections import Counter
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import recherche_offres as ro

st.set_page_config(page_title="Recherche d'offres", page_icon="🔎", layout="wide")
load_dotenv(override=True)


# === Recuperation par source (mise en cache par criteres) ===================
@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
def chercher_jobup(mots_cles, max_offres):
    """jobup.ch — job board suisse francophone, API publique sans clé."""
    config = dict(ro.CONFIG, mots_cles=mots_cles, max_offres=max_offres)
    return ro.rechercher_jobup(config)


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

TOUTES_SOURCES = ["France Travail", "Adzuna", "Jooble", "🇨🇭 jobup.ch"]
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
with st.expander("🔎 Critères de recherche", expanded=not st.session_state.get("lancee")):
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
        "Max d'offres / variante / source", 10, 200, 50, 10,
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
                 "(accents/casse ignorés).",
        )
        exclure_titres = [x.strip() for x in exclusions_txt.splitlines() if x.strip()]

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

go = st.button("🔎 Rechercher", type="primary")
st.divider()

def _tag(offres, terme):
    """Marque chaque offre avec la variante qui l'a ramenée (badge « via »)."""
    for o in offres:
        o.setdefault("_variante", terme)
    return offres

# Une recherche n'est lancée qu'au clic sur "Rechercher". Les offres brutes sont
# mémorisées (session_state) pour survivre aux reruns (clic sur une annonce,
# changement de filtre) sans tout relancer.
if go:
    if not sources:
        st.warning("Sélectionne au moins une source.")
        st.stop()
    if not variantes:
        st.warning("Indique au moins un métier à rechercher.")
        st.stop()

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
    if "🇨🇭 jobup.ch" in sources:
        fetchers.append(("🇨🇭 jobup.ch", lambda t: chercher_jobup(t, max_offres)))

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
                    res = _tag(fn(terme), terme)
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
    st.session_state["lancee"] = True
    # Re-rendu propre, sans le bloc de progression, pour figer la mise en page :
    # évite le "recadrement" au premier clic sur une offre.
    st.rerun()

# Tant qu'aucune recherche n'a été lancée : écran d'accueil
if not st.session_state.get("lancee"):
    st.info("Renseigne tes critères ci-dessus puis clique sur **Rechercher**.")
    st.stop()

brutes = st.session_state["brutes"]
for msg in st.session_state.get("erreurs", []):
    st.warning(msg)
if not brutes:
    st.error("Aucune offre récupérée.")
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
c3.metric("🇨🇭 Suisse", len(suisse))
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


def _bucket_jour(it):
    """Libellé du groupe temporel d'une offre (Aujourd'hui / Hier / date)."""
    d = (it["o"].get("dateCreation", "") or "")[:10]
    if d == AUJOURDHUI:
        return "📅 Aujourd'hui"
    if d == HIER:
        return "📅 Hier"
    return f"📅 {d}" if d else "📅 Date inconnue"


def afficher_detail(it):
    """Panneau de droite : détail complet de l'offre sélectionnée."""
    o = it["o"]
    titre = o.get("intitule", "Sans titre")
    ent = ro.nom_entreprise(o) or "Entreprise non précisée"
    lieu = champ(o, "lieuTravail", "libelle", defaut="-")
    contrat = o.get("typeContratLibelle") or o.get("typeContrat", "")
    d = (o.get("dateCreation", "") or "")[:10]
    salaire = champ(o, "salaire", "libelle")
    url = champ(o, "origineOffre", "urlOrigine")
    src, via = o.get("_source", ""), o.get("_variante", "")
    desc = (o.get("description", "") or "").strip()

    st.markdown(f"## {titre}")
    meta = f"**{ent}** · {lieu} · {contrat}"
    if d:
        meta += f" · {d}"
    if salaire:
        meta += f" · 💶 {salaire}"
    st.markdown(meta)
    if it.get("favori"):
        st.markdown("🎯 **Département favori**")
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
    if url:
        st.link_button("➡️ Voir / postuler", url, type="primary")
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


# Items de liste : texte aligné à gauche et limité à UNE seule ligne (… si trop long)
st.markdown(
    "<style>"
    "section.main div.stButton > button "
    "{ text-align:left; justify-content:flex-start; overflow:hidden; }"
    "section.main div.stButton > button p "
    "{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin:0; }"
    "</style>",
    unsafe_allow_html=True,
)


def vue_liste_detail(items, cle, csv_nom, grouper_par_jour=False):
    """Affichage façon LinkedIn : liste cliquable à gauche, détail à droite.

    grouper_par_jour : insère un séparateur par jour (Aujourd'hui / Hier / date)
    au lieu du séparateur 🆕 récentes / plus anciennes.
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
        with st.container(height=680):
            for i, it in enumerate(items):
                if grouper_par_jour:
                    # Séparateur par jour (Aujourd'hui / Hier / date), en-tête inclus
                    bucket = _bucket_jour(it)
                    if i == 0 or bucket != _bucket_jour(items[i - 1]):
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
                d = (o.get("dateCreation", "") or "")[:10]
                lieu = champ(o, "lieuTravail", "libelle")
                bdg = ("🆕 " if it.get("recent") else "")
                if it.get("favori"):
                    bdg += "🎯 "
                if it.get("rh"):
                    bdg += "👥 "
                if it.get("score"):
                    bdg += f"⭐{it['score']} "
                if lieu:
                    bdg += f"📍{lieu} "
                if d:
                    bdg += f"📅{d} "
                label = f"{bdg}· {titre} — {ent}"
                if st.button(label, key=f"{cle}_{i}", use_container_width=True,
                             type="primary" if i == sel else "secondary"):
                    st.session_state[sel_key] = i
                    st.rerun()
    with col_detail:
        with st.container(height=680):
            afficher_detail(items[sel])


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
                      "po": ro.est_product_owner(o), "couleur": _couleur_offre(o),
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
    BA_TERMES = ("analyst", "business", "amoa", "fonctionnel")
    nba = sum(1 for it in items
              if any(t in ro.normaliser(it["o"].get("intitule", "")) for t in BA_TERMES))
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


france_items = _decorer(francaises)
# Onglet France : on conserve la séparation 🆕 récentes / plus anciennes, et DANS
# chacune de ces deux sections les offres 👥 RH/SIRH remontent en tête, puis le
# classement reprend par score ⭐ puis date.
france_items.sort(
    key=lambda it: (it["recent"], bool(it.get("rh")), it["score"],
                    (it["o"].get("dateCreation", "") or "")[:10]),
    reverse=True,
)
suisse_items = _decorer(suisse)

# Onglet « Perfect » : sous-ensemble France le plus exigeant — score > 2 ⭐ ET
# domaine RH/SIRH 👥 ET département favori 🎯. Tri strict par date (la plus
# récente d'abord), avec séparation Aujourd'hui / Hier / plus ancien.
perfect_items = sorted(
    [it for it in france_items
     if it["score"] > 2 and it.get("rh") and it.get("favori")],
    key=lambda it: (it["o"].get("dateCreation", "") or ""), reverse=True,
)

onglet_perfect, onglet_fr, onglet_ch, onglet_excl, onglet_dash = st.tabs([
    f"💎 Perfect ({len(perfect_items)})",
    f"🎯 France ({len(france_items)})",
    f"🇨🇭 Suisse ({len(suisse_items)})",
    f"🗑️ Exclues ({len(exclues)})",
    "📊 Tableau de bord",
])
with onglet_perfect:
    st.markdown(_legende(perfect_items), unsafe_allow_html=True)
    st.caption("**Perfect** = offres France les plus alignées : score **> 2 ⭐** "
               "**ET** domaine **👥 RH/SIRH** **ET** 🎯 département favori. "
               "Triées par date (plus récente d'abord), séparées Aujourd'hui / Hier.")
    vue_liste_detail(perfect_items, "perfect", "offres_perfect.csv",
                     grouper_par_jour=True)

with onglet_fr:
    st.markdown(_legende(france_items), unsafe_allow_html=True)
    st.caption(f"Tri : 🆕 récentes (≤ {jours_recent} j) d'abord, puis score profil ⭐, "
               "puis date. Couleur de ligne = métier (configurable « mot:couleur » dans "
               "les Filtres). Clique une annonce pour son détail.")
    vue_liste_detail(france_items, "fr", "offres_france.csv")

with onglet_ch:
    st.markdown(_legende(suisse_items), unsafe_allow_html=True)
    st.caption("**Suisse** via jobup.ch + offres suisses des sources France — "
               "**allemand exclu** (romande + anglais). Mêmes filtres et scoring que "
               "France ; tri 🆕 récentes puis score ⭐.")
    vue_liste_detail(suisse_items, "ch", "offres_suisse.csv")

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
            table, use_container_width=True, hide_index=True,
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
