#!/usr/bin/env python3
"""Interface web pour lire les offres d'emploi (France Travail + Adzuna).

Lancement :  streamlit run app.py
"""
import io
import csv
import os

import streamlit as st
from dotenv import load_dotenv

import recherche_offres as ro

st.set_page_config(page_title="Recherche d'offres", page_icon="🔎", layout="wide")
load_dotenv(override=True)


# === Recuperation par source (mise en cache par criteres) ===================
@st.cache_data(show_spinner=False)
def chercher_france_travail(mots_cles, departement, type_contrat, max_offres):
    cid, secret = os.getenv("FT_CLIENT_ID"), os.getenv("FT_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "France Travail : FT_CLIENT_ID / FT_CLIENT_SECRET manquants dans .env"
        )
    token = ro.obtenir_token(cid, secret)
    config = dict(
        ro.CONFIG, mots_cles=mots_cles, departement=departement or None,
        type_contrat=type_contrat or None, max_offres=max_offres,
    )
    offres = ro.rechercher(token, config)
    for o in offres:
        o["_source"] = "France Travail"
    return offres


@st.cache_data(show_spinner=False)
def chercher_adzuna(mots_cles, lieu, type_contrat, max_offres):
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError(
            "Adzuna : ADZUNA_APP_ID / ADZUNA_APP_KEY manquants dans .env "
            "(inscription gratuite sur developer.adzuna.com/signup)"
        )
    config = dict(
        ro.CONFIG, mots_cles=mots_cles, type_contrat=type_contrat or None,
        max_offres=max_offres, where_adzuna=lieu or None,
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


# === Barre laterale : criteres =============================================
st.sidebar.header("Critères de recherche")
TOUTES_SOURCES = ["France Travail", "Adzuna", "Jooble"]
sources = st.sidebar.multiselect(
    "Sources", TOUTES_SOURCES, default=TOUTES_SOURCES,
    help="Les offres des sources cochées sont fusionnées et dédoublonnées.",
)
variantes_txt = st.sidebar.text_area(
    "Métiers recherchés (une variante par ligne)",
    value="\n".join(ro.SYNONYMES_PAR_DEFAUT), height=180,
    help="Chaque ligne lance une recherche distincte. Les résultats sont "
         "fusionnés et dédoublonnés, pour capter les offres titrées avec un "
         "synonyme (ex: « analyste d'affaires »).",
)
variantes = [v.strip() for v in variantes_txt.splitlines() if v.strip()]

lieu_saisi = st.sidebar.text_input(
    "Lieu", value="",
    help="Code département (75, 69…) OU ville/région (Paris, Lyon…). "
         "Vide = toute la France. Traduit automatiquement pour chaque source.",
)
departement, lieu_nom = ro.resoudre_lieu(lieu_saisi)
if lieu_saisi.strip():
    detail = []
    if departement:
        detail.append(f"FT → dépt {departement}")
    if lieu_nom:
        detail.append(f"Adzuna/Jooble → « {lieu_nom} »")
    st.sidebar.caption(" · ".join(detail) if detail else "lieu non reconnu")

contrats = ["", "CDI", "CDD", "MIS"]
type_contrat = st.sidebar.selectbox(
    "Type de contrat", options=contrats,
    index=contrats.index(ro.CONFIG["type_contrat"] or ""),
    format_func=lambda x: x or "Tous",
    help="Pour Adzuna, MIS (intérim) n'est pas filtré (CDI/CDD seulement).",
)
max_offres = st.sidebar.slider(
    "Nombre max d'offres / variante / source", 10, 200, 50, 10,
    help="Plafond par recherche. Avec plusieurs variantes, le total cumulé "
         "peut être bien plus élevé.",
)
st.sidebar.divider()
st.sidebar.subheader("🧹 Filtres")
jours_max = st.sidebar.slider(
    "Ancienneté max (jours)", 0, 90, 7,
    help="Exclut les offres parues il y a plus de N jours. 0 = pas de limite. "
         "Les offres sans date sont conservées.",
)

filtre_pertinence = st.sidebar.checkbox(
    "Titre pertinent obligatoire", value=True,
    help="Ne garde que les offres dont le TITRE contient un des mots ci-dessous. "
         "Élimine le bruit capté via la description seule (développeurs, techniciens…).",
)
if filtre_pertinence:
    inclusions_txt = st.sidebar.text_area(
        "Le titre doit contenir un de ces mots",
        value="\n".join(ro.INCLUSIONS_TITRE_PAR_DEFAUT), height=110,
    )
    inclure_titres = [x.strip() for x in inclusions_txt.splitlines() if x.strip()]
else:
    inclure_titres = None

exclusions_txt = st.sidebar.text_area(
    "Exclure si le titre contient (un mot par ligne)",
    value="\n".join(ro.EXCLUSIONS_TITRE_PAR_DEFAUT), height=120,
    help="Toute offre dont le titre contient un de ces mots est écartée "
         "(accents/casse ignorés). Pour virer les métiers voisins.",
)
exclure_titres = [x.strip() for x in exclusions_txt.splitlines() if x.strip()]

st.sidebar.divider()
st.sidebar.subheader("⭐ Profil Optimum")
profil_txt = st.sidebar.text_area(
    "Profil Optimum (compétences, une par ligne)",
    value="\n".join(ro.PROFIL_OPTIMUM), height=200,
    help="Classe les offres de l'onglet ⭐ Optimum. Chaque ligne vaut son poids : "
         "« terme » = 1, ou « terme:poids » (ex. mutuelle:3). Les domaines (RH, "
         "mutuelle, assurance, banque) sont pondérés plus fort. Titre = ×2.",
)
profil_optimum = [x.strip() for x in profil_txt.splitlines() if x.strip()]

go = st.sidebar.button("🔎 Rechercher", type="primary", use_container_width=True)


# === Page principale ========================================================
st.title("🔎 Recherche d'offres")
st.caption("Recherche multi-variantes, filtrée des ESN, cabinets et doublons.")

if not go:
    st.info("Renseigne tes critères à gauche puis clique sur **Rechercher**.")
    st.stop()

# Recuperation : chaque variante de metier x chaque source
brutes, erreurs = [], []
if not sources:
    st.warning("Sélectionne au moins une source.")
    st.stop()
if not variantes:
    st.warning("Indique au moins un métier à rechercher.")
    st.stop()

def _tag(offres, terme):
    """Marque chaque offre avec la variante qui l'a ramenée (badge « via »)."""
    for o in offres:
        o.setdefault("_variante", terme)
    return offres

# Fonctions de recherche actives selon les sources cochées
fetchers = []
if "France Travail" in sources:
    fetchers.append(("France Travail", lambda t: chercher_france_travail(
        t, departement or "", type_contrat, max_offres)))
if "Adzuna" in sources:
    fetchers.append(("Adzuna", lambda t: chercher_adzuna(
        t, lieu_nom or "", type_contrat, max_offres)))
if "Jooble" in sources:
    fetchers.append(("Jooble", lambda t: chercher_jooble(t, lieu_nom or "", max_offres)))

total = len(variantes) * len(fetchers)
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

for msg in erreurs:
    st.warning(msg)
if not brutes:
    st.error("Aucune offre récupérée.")
    st.stop()

# Filtrage ESN + titre + ancienneté + dedoublonnage (croise les sources)
gardees, exclues, stats = ro.filtrer(brutes, jours_max, exclure_titres, inclure_titres)

if not gardees:
    st.warning("Aucune offre retenue avec ces critères.")
    st.stop()

# Separation France / International
internationales = [o for o in gardees if ro.est_international(o)]
francaises = [o for o in gardees if not ro.est_international(o)]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Récupérées", stats["brutes"])
c2.metric("France", len(francaises))
c3.metric("International", len(internationales))
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
            st.caption("Cabinets de recrutement : *non exclus* (option désactivée)")
    st.caption("Les ESN et signaux sont définis dans le code (recherche_offres.py). "
               "Demande-moi d'en ajouter/retirer à tout moment.")

champs = ["intitule", "entreprise", "lieu", "contrat", "date", "url"]


def afficher_offre(o, match=None, score=None):
    titre = o.get("intitule", "Sans titre")
    ent = ro.nom_entreprise(o) or "Entreprise non précisée"
    lieu = champ(o, "lieuTravail", "libelle", defaut="-")
    contrat = o.get("typeContratLibelle") or o.get("typeContrat", "")
    d = (o.get("dateCreation", "") or "")[:10]
    salaire = champ(o, "salaire", "libelle")
    url = champ(o, "origineOffre", "urlOrigine")
    desc = (o.get("description", "") or "").strip()
    src = o.get("_source", "")
    via = o.get("_variante", "")

    with st.container(border=True):
        st.markdown(f"### {titre}")
        meta = f"**{ent}** · {lieu} · {contrat}"
        if d:
            meta += f" · {d}"
        if salaire:
            meta += f" · 💶 {salaire}"
        st.markdown(meta)
        if match:
            chips = " ".join(f"`{m}`" for m in match)
            tete = f"⭐ **Score {score}**" if score is not None else f"⭐ **{len(match)} compétences**"
            st.markdown(f"{tete} ({len(match)} en commun) : {chips}")
        badges = " ".join(f"`{x}`" for x in (src, f"via {via}" if via else "") if x)
        if badges:
            st.caption(badges)
        if desc:
            with st.expander("Description"):
                st.write(desc)
        if url:
            st.markdown(f"[➡️ Voir l'offre]({url})")


def afficher_liste(offres, nom_fichier):
    st.download_button(
        "⬇️ Télécharger ce résultat (CSV)",
        data=csv_bytes([ro.ligne(o) for o in offres], champs),
        file_name=nom_fichier, mime="text/csv", key=nom_fichier,
        disabled=not offres,
    )
    for o in offres:
        afficher_offre(o)


# Classement Optimum : offres France triées par adéquation avec le profil
optimum = []
for o in francaises:
    sc, comp = ro.score_optimum(o, profil_optimum)
    optimum.append((sc, comp, o))
# Tri : score décroissant, puis date décroissante (offres récentes d'abord)
optimum.sort(key=lambda x: (x[0], (x[2].get("dateCreation", "") or "")[:10]), reverse=True)

onglet_opt, onglet_fr, onglet_int, onglet_exc = st.tabs([
    f"⭐ Optimum ({len(optimum)})",
    f"🇫🇷 France ({len(francaises)})",
    f"🌍 International ({len(internationales)})",
    f"🗑️ Exclues ({len(exclues)})",
])
with onglet_opt:
    st.caption("Offres France classées par adéquation avec ton profil "
               "(compétences en commun, titre compté double). Profil éditable à gauche.")
    avec_match = [t for t in optimum if t[0] > 0]
    st.download_button(
        "⬇️ Télécharger le classement Optimum (CSV)",
        data=csv_bytes([dict(ro.ligne(o), score=sc, competences=", ".join(comp))
                        for sc, comp, o in optimum],
                       champs + ["score", "competences"]),
        file_name="offres_optimum.csv", mime="text/csv", key="optimum_csv",
        disabled=not optimum,
    )
    if not avec_match:
        st.info("Aucune offre ne correspond au profil. Vérifie la liste de compétences.")
    for sc, comp, o in optimum:
        if sc > 0:
            afficher_offre(o, match=comp, score=sc)
with onglet_fr:
    if francaises:
        afficher_liste(francaises, "offres_france.csv")
    else:
        st.info("Aucune offre en France avec ces critères.")
with onglet_int:
    if internationales:
        afficher_liste(internationales, "offres_international.csv")
    else:
        st.info("Aucune offre hors de France.")
with onglet_exc:
    if exclues:
        compte = stats["compte"]
        st.markdown("**Répartition par motif d'exclusion :**")
        for raison, n in sorted(compte.items(), key=lambda x: -x[1]):
            st.markdown(f"- {raison} : **{n}**")
        st.dataframe(exclues, use_container_width=True, hide_index=True)
    else:
        st.info("Aucune offre exclue.")
