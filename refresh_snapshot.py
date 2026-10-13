#!/usr/bin/env python3
"""Génère un instantané (snapshot) JSON des offres brutes, toutes sources.

Lancé toutes les 4 h par GitHub Actions, puis publié sur la branche `data`.
L'application lit ce fichier (au lieu d'interroger les API à chaque visite) :
les données affichées sont donc rafraîchies toutes les 4 h en arrière-plan,
sans consommer de quota à l'ouverture du site.

Réutilise `daily_search.collecter_brutes` (France Travail + Adzuna + Jooble +
Workday des grands groupes) et
ajoute jobup.ch (Suisse) pour alimenter aussi l'onglet 🇨🇭.

Variables d'environnement : FT_CLIENT_ID/SECRET, ADZUNA_APP_ID/KEY, JOOBLE_API_KEY.
Optionnel : MAX_OFFRES (défaut 200), JOURS_MAX (défaut 14).
"""
import json
from datetime import datetime, timezone

from dotenv import load_dotenv

import recherche_offres as ro
import daily_search as ds

load_dotenv(override=True)

FICHIER = "offres_snapshot.json"


def main():
    brutes, erreurs = ds.collecter_brutes()  # FT + Adzuna + Jooble + Workday (variantes)
    # jobup.ch (Suisse romande) pour l'onglet 🇨🇭 — _source = "jobup.ch" déjà posé.
    for variante in ro.SYNONYMES_PAR_DEFAUT:
        try:
            cfg = dict(ro.CONFIG, mots_cles=variante, max_offres=ds.MAX_OFFRES)
            for o in ro.rechercher_jobup(cfg):
                o["_variante"] = variante
                brutes.append(o)
        except Exception as e:  # noqa: BLE001
            erreurs.append(f"jobup « {variante} » : {e}")

    snap = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offres": brutes,
        "erreurs": erreurs,
    }
    with open(FICHIER, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    print(f"{len(brutes)} offres -> {FICHIER}")
    for e in erreurs:
        print(" [!]", e)


if __name__ == "__main__":
    main()
