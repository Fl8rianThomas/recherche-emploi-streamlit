#!/usr/bin/env python3
"""Génère un instantané (snapshot) JSON des offres brutes, toutes sources.

Lancé toutes les 4 h par GitHub Actions, puis publié sur la branche `data`.
L'application lit ce fichier (au lieu d'interroger les API à chaque visite) :
les données affichées sont donc rafraîchies toutes les 4 h en arrière-plan,
sans consommer de quota à l'ouverture du site.

Réutilise `daily_search.collecter_brutes` (France Travail + Adzuna + Jooble +
sites carrières des grands groupes : Workday, LVMH, BPCE) et
ajoute jobup.ch (Suisse) pour alimenter aussi l'onglet 🇨🇭.

Variables d'environnement : FT_CLIENT_ID/SECRET, ADZUNA_APP_ID/KEY, JOOBLE_API_KEY.
Optionnel : MAX_OFFRES (défaut 200), JOURS_MAX (défaut 14).
"""
import json
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

import recherche_offres as ro
import sites_carrieres as sc
import daily_search as ds

load_dotenv(override=True)

FICHIER = "offres_snapshot.json"
PRECEDENT = "precedent.json"   # instantané précédent (récupéré par le workflow)
HISTORIQUE = 12                # passages conservés : 2 jours à raison de 6 par jour
SOURCES_API = ("France Travail", "Adzuna", "Jooble", "jobup.ch")
SEUIL_MUETTE = 5               # volume « normal » minimal avant la chute (évite les
                               # fausses alertes des petits sites qui oscillent 0-2)


def sources_muettes(historique):
    """Sources à 0 offre brute sur les 3 derniers passages alors qu'elles en
    renvoyaient au moins SEUIL_MUETTE avant : panne silencieuse probable."""
    if len(historique) < 4:
        return []
    recents, anciens = historique[-3:], historique[:-3]
    sources = set().union(*historique)
    return sorted(s for s in sources
                  if all(h.get(s, 0) == 0 for h in recents)
                  and any(h.get(s, 0) >= SEUIL_MUETTE for h in anciens))


def categories_entreprises(brutes, cache):
    """{cle_entreprise: « GE » / « ETI » / « PME » / None} des employeurs des offres
    France au titre pertinent (filtres par défaut), d'après l'INSEE.

    `cache` (snapshot précédent) : seuls les nouveaux employeurs interrogent l'API.
    Cabinets de recrutement et agrégateurs ignorés (leur taille ne dit rien de
    l'employeur final). Erreur réseau : l'employeur sera réessayé au passage suivant.
    """
    inclus = ds._inclusions_mots()
    gardees, _e, _s = ro.filtrer(brutes, ds.JOURS_MAX, ro.EXCLUSIONS_TITRE_PAR_DEFAUT,
                                 inclus, ignorer_esn=True)
    noms = {}
    for o in gardees:
        if o.get("_source") == "jobup.ch" or ro.est_suisse(o) or ro.est_international(o):
            continue
        nom = ro.nom_entreprise(o)
        norm = ro.normaliser(nom)
        if ro.REGEX_AGENCES.search(norm) or ro.REGEX_AGREGATEURS.search(norm):
            continue
        cle = ro.cle_entreprise(nom)
        if cle:
            noms.setdefault(cle, nom)
    categories, nouvelles = {}, 0
    for cle, nom in noms.items():
        if cle in cache:
            categories[cle] = cache[cle]
            continue
        try:
            categories[cle] = ro.categorie_entreprise(nom)
            nouvelles += 1
        except Exception as e:  # noqa: BLE001
            print(f" [!] catégorie INSEE « {nom} » : {e}")
        time.sleep(0.15)                       # limite de l'API : ~7 requêtes/s
    print(f"Catégories INSEE : {len(categories)} employeurs ({nouvelles} interrogés), "
          f"{sum(1 for c in categories.values() if c == 'GE')} GE")
    return categories


def main():
    brutes, erreurs = ds.collecter_brutes()  # FT + Adzuna + Jooble + sites carrières
    # jobup.ch (Suisse romande) pour l'onglet 🇨🇭 — _source = "jobup.ch" déjà posé.
    for variante in ro.SYNONYMES_PAR_DEFAUT:
        try:
            cfg = dict(ro.CONFIG, mots_cles=variante, max_offres=ds.MAX_OFFRES)
            for o in ro.rechercher_jobup(cfg):
                o["_variante"] = variante
                brutes.append(o)
        except Exception as e:  # noqa: BLE001
            erreurs.append(f"jobup « {variante} » : {e}")

    # Volumes bruts par source (sites carrières : avant tout filtre) + historique
    # des passages précédents -> détection des sources devenues muettes.
    volumes = sc.volumes_bruts()
    for src in SOURCES_API:
        volumes[src] = sum(1 for o in brutes if o.get("_source") == src)
    try:
        with open(PRECEDENT, encoding="utf-8") as f:
            precedent = json.load(f)
    except (OSError, ValueError):
        precedent = {}
    historique = (precedent.get("historique_sources") or [])[-(HISTORIQUE - 1):] + [volumes]
    muettes = sources_muettes(historique)
    erreurs += [f"{s} : aucune offre sur les 3 derniers passages alors qu'elle en "
                "renvoyait avant — site modifié ?" for s in muettes]

    # Grande entreprise (GE) INSEE : critère de la carte (ro.rang_carte), posé sur
    # chaque offre (« _categorie ») pour que l'application n'ait rien à interroger.
    categories = categories_entreprises(brutes, precedent.get("categories_entreprises") or {})
    for o in brutes:
        cat = categories.get(ro.cle_entreprise(ro.nom_entreprise(o)))
        if cat:
            o["_categorie"] = cat

    snap = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offres": brutes,
        "erreurs": erreurs,
        "historique_sources": historique,
        "sources_muettes": muettes,
        "categories_entreprises": categories,
    }
    with open(FICHIER, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    print(f"{len(brutes)} offres -> {FICHIER} | sources muettes : {muettes or 'aucune'}")
    for e in erreurs:
        print(" [!]", e)


if __name__ == "__main__":
    main()
