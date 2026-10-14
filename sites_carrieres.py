#!/usr/bin/env python3
"""Sources « sites carrières » des grands groupes (API publiques, sans clé).

Workday (~60 tenants), LVMH (Algolia), BPCE, Jibe (AXA, Schneider), SmartRecruiters,
Phenom, Crédit Agricole, Engie, Radancy (Vinci, Veolia, L'Oréal), DigitalRecruiters
(Cegedim), SuccessFactors HTML (Servier, Novo Nordisk…), Ashby (e-santé).
Point d'entrée : rechercher_sites_carrieres(config, variantes) -> (offres, erreurs),
toutes les requêtes en parallèle ; volumes_bruts() sert à détecter les sources
devenues muettes (voir refresh_snapshot.py).

Le cœur (normalisation, filtres, scoring, France Travail / Adzuna / Jooble) reste
dans recherche_offres.py, dont ce module réutilise quelques utilitaires.
"""
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import requests

from recherche_offres import (TRANSITOIRES, _get_avec_retries, mots_cles_recherche,
                              normaliser, regles_exclusion, terme_exclu)

# === 9. SOURCE WORKDAY (sites carrieres des grands groupes) =================
# Beaucoup de grands groupes publient leurs offres sur Workday, qui expose une API
# JSON publique (celle du site carriere) : recherche par mots-cles + filtre pays.
# Ajouter un groupe = ajouter (nom, URL publique du site carriere Workday[, facette]).
# Facette pays : le NOM du filtre varie selon le tenant (defaut « locationCountry ») ;
# None = pas de filtre (site 100 % France ; le filtre y renverrait une erreur 400) ;
# "auto" = detection (facette pays si elle existe, sinon les LIEUX « …, France »,
# « France - … », « FRA… » d'une facette de type locations) ; tuple de libelles =
# liste blanche de lieux (tenants dont les lieux ne citent pas le pays).
# Astuce : https://{tenant}.wdN.myworkdayjobs.com/robots.txt liste les sites d'un tenant.
WORKDAY_GROUPES = [
    ("Airbus", "https://ag.wd3.myworkdayjobs.com/Airbus"),
    ("Thales", "https://thales.wd3.myworkdayjobs.com/Careers"),
    ("Sanofi", "https://sanofi.wd3.myworkdayjobs.com/SanofiCareers"),
    ("Chanel", "https://cc.wd3.myworkdayjobs.com/ChanelCareers"),
    ("Renault", "https://alliancewd.wd3.myworkdayjobs.com/renault-group-careers"),
    ("Richemont", "https://richemont.wd3.myworkdayjobs.com/Richemont"),
    ("Air Liquide", "https://airliquidehr.wd3.myworkdayjobs.com/AirLiquideExternalCareer"),
    ("Michelin", "https://michelinhr.wd3.myworkdayjobs.com/Michelin", "Location_Country"),
    ("Pierre Fabre", "https://pierrefabre.wd3.myworkdayjobs.com/External_Career_Site"),
    ("Valeo", "https://valeo.wd3.myworkdayjobs.com/valeo_jobs"),
    ("Eiffage", "https://eiffage.wd3.myworkdayjobs.com/Eiffage_Careers"),
    ("ArianeGroup", "https://arianegroup.wd3.myworkdayjobs.com/EXTERNALALL"),
    ("LFB", "https://lfb.wd3.myworkdayjobs.com/LFB_External_Careers"),
    ("Ipsen", "https://ipsen.wd103.myworkdayjobs.com/Ipsen_Careers"),
    ("AG2R La Mondiale", "https://ag2rlamondiale.wd3.myworkdayjobs.com/Candidats", None),
    ("Rothschild & Co", "https://rothschildandco.wd3.myworkdayjobs.com/Rothschildandco_Lateral"),
    ("Ayvens", "https://ayvens.wd3.myworkdayjobs.com/AyvensCareers"),
    ("Ardian", "https://ardian.wd103.myworkdayjobs.com/ArdianCareers"),
    ("Marsh McLennan", "https://mmc.wd1.myworkdayjobs.com/MMC", "Location_Country"),
    ("Citi", "https://citi.wd5.myworkdayjobs.com/2", "Country_and_Jurisdiction"),
    ("Imerys", "https://imerys.wd3.myworkdayjobs.com/IMERYS-Careers", "Country"),
    ("PVH", "https://pvh.wd1.myworkdayjobs.com/PVH_Careers", "Location_Country"),
    # Santé (labos, medtech, CRO) — Centre-Val de Loire : Expanscience (Épernon),
    # Henry Schein / Stryker / Smith+Nephew (Tours), Essity (Gien), Pfizer (Amboise).
    ("Expanscience", "https://expanscience.wd3.myworkdayjobs.com/Expanscience_Careers", "auto"),
    ("Henry Schein", "https://henryschein.wd1.myworkdayjobs.com/External_Careers"),
    ("Essity", "https://essity.wd3.myworkdayjobs.com/Job_opportunities", "Country"),
    ("Danaher", "https://danaher.wd1.myworkdayjobs.com/DanaherJobs"),
    ("Stryker", "https://stryker.wd1.myworkdayjobs.com/StrykerCareers", "Location_Country"),
    ("Smith+Nephew", "https://smithnephew.wd5.myworkdayjobs.com/External", "Country"),
    ("Pfizer", "https://pfizer.wd1.myworkdayjobs.com/PfizerCareers", "Location_Country"),
    ("Roche", "https://roche.wd3.myworkdayjobs.com/roche-ext", "auto"),
    ("GSK", "https://gsk.wd5.myworkdayjobs.com/GSKCareers", "Location_Country"),
    ("Johnson & Johnson", "https://jj.wd5.myworkdayjobs.com/JJ", "auto"),
    ("Becton Dickinson", "https://bdx.wd1.myworkdayjobs.com/EXTERNAL_CAREER_SITE_FRANCE", None),
    ("Thermo Fisher", "https://thermofisher.wd5.myworkdayjobs.com/ThermoFisherCareers", "auto"),
    ("IQVIA", "https://iqvia.wd1.myworkdayjobs.com/IQVIA", "Location_Country"),
    ("Abbott", "https://abbott.wd5.myworkdayjobs.com/abbottcareers", "Location_Country"),
    ("GE HealthCare", "https://gehc.wd5.myworkdayjobs.com/GEHC_ExternalSite",
     ("Buc", "Toulouse", "Tremblay-en-France")),
    ("Biocodex", "https://biocodex.wd502.myworkdayjobs.com/Biocodex_Careers"),
    ("Opella", "https://opella.wd103.myworkdayjobs.com/OpellaCareers"),
    ("Sartorius", "https://sartorius.wd3.myworkdayjobs.com/sartoriuscareers", "Country"),
    ("Syneos Health", "https://syneoshealth.wd12.myworkdayjobs.com/Syneos_Health_External_Site", "auto"),
    ("Medline", "https://medline.wd5.myworkdayjobs.com/Medline", "Country"),
    ("Lonza", "https://lonza.wd3.myworkdayjobs.com/Lonza_Careers", "auto"),
    ("Kenvue", "https://kenvue.wd5.myworkdayjobs.com/kenvue", "auto"),
    ("Fresenius", "https://freseniusmedicalcare.wd3.myworkdayjobs.com/fme", "auto"),
    ("Viatris", "https://viatris.wd5.myworkdayjobs.com/External", "Country"),
    ("Baxter", "https://baxter.wd1.myworkdayjobs.com/baxter", "Country"),
    ("Novartis", "https://novartis.wd3.myworkdayjobs.com/Novartis_Careers"),
    ("Sandoz", "https://sandoz.wd103.myworkdayjobs.com/Sandoz_Careers", ("Levallois HQ (Sandoz)",)),
    ("Alcon", "https://alcon.wd5.myworkdayjobs.com/careers_alcon", "auto"),
    ("Edwards Lifesciences", "https://edwards.wd5.myworkdayjobs.com/EdwardsCareers", "auto"),
    ("Catalent", "https://catalent.wd1.myworkdayjobs.com/External", "auto"),
    ("Medtronic", "https://medtronic.wd1.myworkdayjobs.com/MedtronicCareers", "auto"),
    ("Revvity", "https://revvity.wd103.myworkdayjobs.com/External", "auto"),
    ("Amgen", "https://amgen.wd1.myworkdayjobs.com/Careers", "LocationCountry"),
    ("Zentiva", "https://zentiva.wd3.myworkdayjobs.com/Zentiva", "auto"),
    ("Biogen", "https://biibhr.wd3.myworkdayjobs.com/external", "auto"),
    ("Eli Lilly", "https://lilly.wd115.myworkdayjobs.com/LLY", "auto"),
    ("Takeda", "https://takeda.wd502.myworkdayjobs.com/External", "auto"),
    ("MSD", "https://msd.wd5.myworkdayjobs.com/SearchJobs", "auto"),
    ("AstraZeneca", "https://astrazeneca.wd3.myworkdayjobs.com/Careers", "auto"),
    ("Philips", "https://philips.wd3.myworkdayjobs.com/jobs-and-careers", "auto"),
]
WORKDAY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"   # id pays « France » (commun aux tenants)
# Lieux francais d'une facette « locations » : « Lyon, France », « France - Paris »,
# « FRA-EPE ». Jamais « Fran » seul (Frankfurt, Franklin, San Francisco).
_RE_LIEU_FRANCE = re.compile(r"(,\s*france\s*$|^france\b|^fra\b)", re.I)
_workday_filtres = {}   # cache par process : tenant -> appliedFacets « France »

def _workday_feuilles(facettes, parent=None):
    """(parametre, id, libelle) de chaque valeur de l'arbre de facettes Workday."""
    for f in facettes or []:
        fp = f.get("facetParameter", parent)
        for v in f.get("values") or []:
            if v.get("facetParameter") and "values" in v:
                yield from _workday_feuilles([v], fp)
            else:
                yield fp, v.get("id"), (v.get("descriptor") or "").strip()

def _workday_filtre_france(api, nom, facette):
    """appliedFacets qui restreint un tenant a la France (voir WORKDAY_GROUPES)."""
    if facette is None:
        return {}
    if isinstance(facette, str) and facette != "auto":
        return {facette: [WORKDAY_FRANCE]}
    if api not in _workday_filtres:
        data = _post_workday(api + "/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0,
                                             "searchText": ""}, nom)
        feuilles = list(_workday_feuilles(data.get("facets")))
        pays = [fp for fp, i, _l in feuilles if i == WORKDAY_FRANCE]
        if facette == "auto" and pays:
            filtre = {pays[0]: [WORKDAY_FRANCE]}
        else:
            if facette == "auto":
                garde = lambda lib: bool(_RE_LIEU_FRANCE.search(lib))
            else:
                blanche = {normaliser(x) for x in facette}
                garde = lambda lib: normaliser(lib) in blanche
            lieux = {}
            for fp, i, lib in feuilles:
                if re.search(r"loc|city|region|country", fp or "", re.I) and garde(lib):
                    lieux.setdefault(fp, []).append(i)
            if not lieux:
                raise RuntimeError(f"Workday {nom} : aucun lieu France dans les facettes")
            fp = max(lieux, key=lambda k: len(lieux[k]))
            filtre = {fp: lieux[fp]}
        _workday_filtres[api] = filtre
    return _workday_filtres[api]
# Stages / alternances / VIE : ecartes (on vise des CDI, comme les autres sources).
_RE_HORS_CDI = re.compile(
    r"\b(stage|stagiaire|intern|internship|alternance|alternant|apprenti|apprentie"
    r"|apprentissage|apprenticeship|trainee|work[- ]study|v\.?i\.?e)\b")
# En-tete « navigateur » : certains sites carrieres refusent les clients sans User-Agent.
_UA_NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

def _nom_groupe(marque, groupe):
    """« Marque (Groupe) » (ex. « Guerlain (LVMH) ») sauf si la marque cite deja le
    groupe : l'onglet 🏢 reconnait ainsi toute filiale via le nom du groupe."""
    marque = (marque or "").strip() or groupe
    deja = re.search(r"\b" + re.escape(normaliser(groupe)) + r"\b", normaliser(marque))
    return marque if deja else f"{marque} ({groupe})"   # « AccorHotel » -> « … (Accor) »

def _iso(s):
    """« 2026-09-22T10:24:00+0000 » -> « …+00:00 » (fuseau lisible par fromisoformat)."""
    return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", (s or "").strip())

def _workday_api(url_site):
    """« https://ag.wd3.myworkdayjobs.com/fr-FR/Airbus » -> base de l'API cxs."""
    m = re.match(r"(https://([^./]+)\.[^/]+)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url_site)
    if not m:
        raise ValueError(f"URL Workday non reconnue : {url_site}")
    return f"{m.group(1)}/wday/cxs/{m.group(2)}/{m.group(3)}"

def _workday_age_jours(posted_on):
    """« Posted Today » -> 0, « Posted 3 Days Ago » -> 3, « 30+ Days » -> 30."""
    t = (posted_on or "").lower()
    if "today" in t or "aujourd" in t:
        return 0
    if "yesterday" in t or "hier" in t:
        return 1
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else None

def _html_vers_texte(h):
    """Description HTML Workday -> texte brut (sauts de ligne conserves)."""
    t = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d)>", "\n", h or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()

_workday_details = {}   # cache par process : une offre trouvee par plusieurs variantes

def _workday_detail(api, chemin):
    """Fiche detaillee (description, date, URL). {} si indisponible : l'offre reste."""
    if chemin not in _workday_details:
        try:
            r = _get_avec_retries(api + chemin, None, timeout=20)
            info = r.json().get("jobPostingInfo") if r.status_code == 200 else None
        except (RuntimeError, ValueError):
            info = None
        if info is None:
            return {}                             # pas mis en cache : retente plus tard
        _workday_details[chemin] = info
    return _workday_details[chemin]

def _post_workday(url, corps, nom, tentatives=3):
    """POST de recherche Workday avec reessais (reseau, 5xx/429, reponse non JSON)."""
    derniere = None
    for n in range(tentatives):
        try:
            r = requests.post(url, json=corps, timeout=20)
            if r.status_code == 200:
                return r.json()
            derniere = f"HTTP {r.status_code}"
            if r.status_code not in TRANSITOIRES:
                break
        except (requests.exceptions.RequestException, ValueError) as e:
            derniere = str(e)[:80]
        if n < tentatives - 1:
            time.sleep(1.5 * (n + 1))
    raise RuntimeError(f"Workday {nom} : {derniere}")

# Volume BRUT renvoye par chaque site (avant tout filtre) au dernier passage de
# rechercher_sites_carrieres : un site qui tombe a 0 plusieurs passages de suite
# alors qu'il renvoyait des offres a probablement change -> refresh_snapshot.py.
_volumes = {}
_volumes_verrou = threading.Lock()
_SITES_SANS_DESCRIPTION = {"Crédit Agricole"}   # liste sans description, par conception

def _compter_brut(site, n):
    with _volumes_verrou:
        _volumes[site] = _volumes.get(site, 0) + n

def volumes_bruts():
    """{site: nombre d'offres brutes} du dernier passage de rechercher_sites_carrieres."""
    with _volumes_verrou:
        return dict(_volumes)

def _prefiltre_titres(config):
    """(inclus, exclus) normalises depuis config["inclure_titres" / "exclure_titres"].

    Pre-filtre des sites carrieres : les memes regles sont de toute facon
    re-appliquees ensuite par filtrer() ; ici elles evitent des requetes inutiles.
    """
    return ([normaliser(t) for t in config.get("inclure_titres") or [] if t.strip()],
            regles_exclusion(config.get("exclure_titres")))

def _titre_ok(titre, inclus, exclus, contrat=""):
    """Titre pertinent, non exclu, et pas un stage/alternance (titre ou contrat).

    exclus : regles issues de regles_exclusion (via _prefiltre_titres)."""
    tn = normaliser(titre)
    if inclus and not any(t in tn for t in inclus):
        return False
    if _RE_HORS_CDI.search(tn + " " + normaliser(contrat)):
        return False
    return not terme_exclu(tn, exclus)

def rechercher_workday(config, nom, url_site, facette="locationCountry"):
    """Offres France d'un site carriere Workday (format interne, entreprise = nom).

    facette : filtre France du tenant (cf. WORKDAY_GROUPES : nom, None, "auto", lieux).
    config["inclure_titres"] / ["exclure_titres"] (optionnels) : pre-filtre sur le
    titre AVANT d'ouvrir la fiche detaillee (1 requete par offre) -> ~5x plus rapide.
    """
    api = _workday_api(url_site)
    jours_max = config.get("jours_max")
    inclus, exclus = _prefiltre_titres(config)
    offres, offset, par_page = [], 0, 20          # l'API plafonne limit a 20
    total = None                                  # renvoye seulement en page 1
    pages_vides = 0                               # pages consecutives sans titre pertinent
    while len(offres) < config["max_offres"]:
        corps = {"appliedFacets": _workday_filtre_france(api, nom, facette),
                 "limit": par_page, "offset": offset,
                 "searchText": mots_cles_recherche(config["mots_cles"])}
        data = _post_workday(api + "/jobs", corps, nom)
        if total is None:
            total = data.get("total") or 0
        lot = data.get("jobPostings") or []
        _compter_brut(nom, len(lot))
        if offset == 0 and total and not lot:      # total annonce mais liste vide
            raise RuntimeError(f"Workday {nom} : {total} offres annoncées mais liste "
                               "vide (API modifiée ?)")
        pertinents = 0
        for j in lot:
            titre = j.get("title", "") or ""
            age = _workday_age_jours(j.get("postedOn"))
            if inclus and not any(t in normaliser(titre) for t in inclus):
                continue
            pertinents += 1
            if not _titre_ok(titre, (), exclus):
                continue
            if jours_max and age is not None and age > jours_max:
                continue
            d = _workday_detail(api, j.get("externalPath", ""))
            lieu = d.get("location") or j.get("locationsText") or ""
            offres.append({
                "intitule": d.get("title") or titre,
                "entreprise": {"nom": nom},
                "lieuTravail": {"libelle": f"{lieu}, France" if lieu else "France"},
                "typeContratLibelle": d.get("timeType", "") or "",
                "typeContrat": "",
                "dateCreation": d.get("startDate") or (
                    (date.today() - timedelta(days=age)).isoformat() if age is not None else ""),
                "description": _html_vers_texte(d.get("jobDescription")),
                "origineOffre": {"urlOrigine": d.get("externalUrl")
                                 or url_site.rstrip("/") + (j.get("externalPath") or "")},
                "salaire": {"libelle": ""},
                "_source": "Workday",
            })
        offset += par_page
        # Tri par PERTINENCE et recherche « OU » sur les mots (« product manager »
        # -> 872 offres chez Thales) : 2 pages de suite sans aucun titre pertinent
        # signalent la fin du signal utile. Plafond 10 pages (200 offres).
        pages_vides = 0 if pertinents else pages_vides + 1
        if not lot or offset >= total or offset >= 200 or (inclus and pages_vides >= 2):
            break
        time.sleep(0.3)
    return offres[: config["max_offres"]]

# === 10. SOURCE LVMH (Dior, Louis Vuitton, Sephora, Moet Hennessy...) =======
# Le site lvmh.com interroge un index Algolia PUBLIC (cle de recherche presente dans
# son JavaScript) : toutes les Maisons, filtre pays France, fiche complete d'un coup.
LVMH_ALGOLIA_URL = "https://SDMQTD2J9T-dsn.algolia.net/1/indexes/PRD-fr-fr/query"
LVMH_ALGOLIA_HEADERS = {"X-Algolia-Application-Id": "SDMQTD2J9T",
                        "X-Algolia-API-Key": "a5c6f4c87dea9aac0732631cd87583b2"}

def rechercher_lvmh(config):
    """Offres France de toutes les Maisons LVMH (entreprise = « Maison (LVMH) »)."""
    inclus, exclus = _prefiltre_titres(config)
    jours_max = config.get("jours_max")
    limite = (datetime.now(timezone.utc) - timedelta(days=jours_max)).timestamp() \
        if jours_max else None
    offres, page = [], 0
    while len(offres) < config["max_offres"]:
        params = urlencode({"query": mots_cles_recherche(config["mots_cles"]),
                            "filters": 'category:job AND countryRegion:"France"',
                            "hitsPerPage": 100, "page": page})
        r = requests.post(LVMH_ALGOLIA_URL, headers=LVMH_ALGOLIA_HEADERS,
                          json={"params": params}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"LVMH {r.status_code}")
        data = r.json()
        _compter_brut("LVMH", len(data.get("hits") or []))
        for h in data.get("hits") or []:
            ts = int(h.get("publicationTimestamp") or 0)
            if limite and ts and ts < limite:
                continue
            if not _titre_ok(h.get("name", ""), inclus, exclus, h.get("contract", "")):
                continue
            maison = h.get("maison") or "LVMH"
            offres.append({
                "intitule": h.get("name", ""),
                "entreprise": {"nom": _nom_groupe(maison, "LVMH")},
                "lieuTravail": {"libelle": ", ".join(
                    x for x in (h.get("city"), h.get("regionState")) if x) or "France"},
                "typeContratLibelle": h.get("contract", "") or "",
                "typeContrat": "CDI" if h.get("contract") == "CDI" else "",
                # Horodatage = minuit heure de Paris -> date de Paris (pas UTC, J-1).
                "dateCreation": datetime.fromtimestamp(ts, ZoneInfo("Europe/Paris"))
                                .date().isoformat() if ts else "",
                "description": _html_vers_texte("\n".join(
                    h.get(k) or "" for k in ("description", "jobResponsabilities", "profile"))),
                "origineOffre": {"urlOrigine": h.get("link", "") or ""},
                "salaire": {"libelle": ""},
                "_source": "LVMH",
            })
        page += 1
        if page >= (data.get("nbPages") or 0):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 11. SOURCE BPCE (Natixis, Banque Populaire, Caisse d'Epargne...) =======
# Site recrutement.bpce.fr (WordPress) : endpoint REST JSON public de sa recherche.
BPCE_URL = "https://recrutement.bpce.fr/app/wp-json/bpce/v1/search/jobs"

def rechercher_bpce(config):
    """Offres France du Groupe BPCE (entreprise = « Marque (BPCE) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = (date.today() - timedelta(days=config["jours_max"])).isoformat() \
        if config.get("jours_max") else None
    offres, depart, taille = [], 0, 100
    while len(offres) < config["max_offres"]:
        corps = {"lang": "fr", "keyword": mots_cles_recherche(config["mots_cles"]),
                 "tax_country": "france", "external": False, "from": depart, "size": taille}
        r = requests.post(BPCE_URL, json=corps, timeout=30,
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"BPCE {r.status_code}")
        data = r.json().get("data") or {}
        lot = data.get("items") or []
        _compter_brut("BPCE", len(lot))
        for it in lot:
            titre = it.get("title", "") or ""
            contrat = ", ".join(it.get("contract") or [])
            jour = it.get("date") or ""
            if limite and jour and jour < limite:
                continue
            if not _titre_ok(titre, inclus, exclus, contrat):
                continue
            marque = (it.get("brand") or ["BPCE"])[0]
            loc = (it.get("localisations") or [{}])[0] or {}
            lieu = it.get("localisation") or loc.get("city") or ""
            if loc.get("zipcode"):
                lieu += f" ({loc['zipcode']})"
            if loc.get("region"):
                lieu += f", {loc['region']}"
            lien = (it.get("link") or {}).get("url") or ""
            offres.append({
                "intitule": titre,
                "entreprise": {"nom": _nom_groupe(marque, "BPCE")},
                "lieuTravail": {"libelle": lieu or "France"},
                "typeContratLibelle": contrat,
                "typeContrat": "CDI" if contrat == "CDI" else "",
                "dateCreation": jour,
                "description": _html_vers_texte(it.get("description")),
                "origineOffre": {"urlOrigine": "https://recrutement.bpce.fr" + lien
                                 if lien.startswith("/") else lien},
                "salaire": {"libelle": ""},
                "_source": "BPCE",
            })
        depart += taille
        if not lot or depart >= (data.get("total") or 0):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

def _limite_iso(config):
    """Date ISO (AAAA-MM-JJ) en deca de laquelle une offre est trop ancienne, ou None."""
    j = config.get("jours_max")
    return (date.today() - timedelta(days=j)).isoformat() if j else None

# === 12. SOURCE iCIMS JIBE (AXA, Schneider Electric) ========================
# Sites carrieres « Jibe » : API JSON publique /api/jobs (filtre pays + mots-cles),
# description complete incluse.
JIBE_GROUPES = [
    ("AXA", "https://jobs.axa.com"),
    ("Schneider Electric", "https://careers.se.com"),
]

def rechercher_jibe(config, nom, base):
    """Offres France d'un site carriere Jibe (entreprise = « Entite (Groupe) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    offres, page = [], 1
    while len(offres) < config["max_offres"]:
        params = {"page": page, "limit": 100, "country": "France",
                  "keywords": mots_cles_recherche(config["mots_cles"])}
        r = _get_avec_retries(base + "/api/jobs", params, timeout=25,
                              headers={"User-Agent": _UA_NAVIGATEUR, "Accept": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"{nom} {r.status_code}")
        data = r.json()
        lot = data.get("jobs") or []
        _compter_brut(nom, len(lot))
        for x in lot:
            d = x.get("data") or {}
            contrat = " ".join((d.get("tags1") or []) + (d.get("tags2") or []))
            publie = _iso(d.get("posted_date"))
            if limite and publie and publie[:10] < limite:
                continue
            if not _titre_ok(d.get("title", ""), inclus, exclus, contrat):
                continue
            ville = (d.get("city") or "").title()
            cp = d.get("postal_code") or ""
            offres.append({
                "intitule": d.get("title", ""),
                "entreprise": {"nom": _nom_groupe((d.get("tags3") or [""])[0]
                                                  if nom == "AXA" else "", nom)},
                "lieuTravail": {"libelle": f"{ville} ({cp}), France" if cp
                                else f"{ville}, France" if ville else "France"},
                "typeContratLibelle": contrat,
                "typeContrat": "CDI" if "permanent" in normaliser(contrat) else "",
                "dateCreation": publie,
                "description": _html_vers_texte(d.get("description")),
                "origineOffre": {"urlOrigine": f"{base}/jobs/{d['slug']}" if d.get("slug")
                                 else d.get("apply_url", "")},
                "salaire": {"libelle": ""},
                "_source": nom,
            })
        page += 1
        if not lot or (page - 1) * 100 >= (data.get("totalCount") or 0):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 13. SOURCE SMARTRECRUITERS (Eurofins, Coface, Bosch, AbbVie, Biogaran) =
# API publique officielle : liste filtree pays + fiche detaillee (description).
SMARTRECRUITERS_URL = "https://api.smartrecruiters.com/v1/companies/{id}/postings"
SMARTRECRUITERS_GROUPES = [          # (nom affiche, identifiant SmartRecruiters)
    ("Eurofins", "Eurofins"), ("Coface", "Coface"), ("Bosch", "BoschGroup"),
    ("AbbVie", "AbbVie"), ("Biogaran", "Biogaran"),
]

def rechercher_smartrecruiters(config, nom, ident):
    """Offres France d'une entreprise SmartRecruiters (entreprise = nom)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    url = SMARTRECRUITERS_URL.format(id=ident)
    entetes = {"User-Agent": _UA_NAVIGATEUR}
    offres, depart = [], 0
    while len(offres) < config["max_offres"]:
        r = _get_avec_retries(url, {"country": "fr", "limit": 100, "offset": depart,
                                    "q": mots_cles_recherche(config["mots_cles"])},
                              headers=entetes)
        if r.status_code != 200:
            raise RuntimeError(f"SmartRecruiters {ident} {r.status_code}")
        data = r.json()
        lot = data.get("content") or []
        _compter_brut(nom, len(lot))
        for p in lot:
            contrat = " ".join(filter(None, [
                (p.get("typeOfEmployment") or {}).get("label"),
                (p.get("experienceLevel") or {}).get("label")]
                + [c.get("valueLabel") for c in p.get("customField") or []
                   if "contract" in normaliser(c.get("fieldLabel"))]))
            publie = p.get("releasedDate") or ""
            if limite and publie and publie[:10] < limite:
                continue
            if not _titre_ok(p.get("name", ""), inclus, exclus, contrat):
                continue
            try:   # fiche detaillee : description (sections de l'annonce) + URL publique
                d = _get_avec_retries(f"{url}/{p['id']}", None, headers=entetes).json()
            except (RuntimeError, ValueError):
                d = {}
            sections = ((d.get("jobAd") or {}).get("sections") or {}).values()
            loc = p.get("location") or {}
            offres.append({
                "intitule": p.get("name", ""),
                "entreprise": {"nom": _nom_groupe((p.get("company") or {}).get("name"), nom)},
                "lieuTravail": {"libelle": ", ".join(filter(None, [
                    loc.get("city"), loc.get("postalCode"), "France"]))},
                "typeContratLibelle": contrat,
                "typeContrat": "",
                "dateCreation": publie,
                "description": _html_vers_texte("\n".join(
                    s.get("text") or "" for s in sections if isinstance(s, dict))),
                "origineOffre": {"urlOrigine": d.get("postingUrl")
                                 or f"https://jobs.smartrecruiters.com/{ident}/{p.get('id')}"},
                "salaire": {"libelle": ""},
                "_source": "SmartRecruiters",
            })
        depart += 100
        if not lot or depart >= (data.get("totalFound") or 0):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 14. SOURCE PHENOM (Allianz, Siemens Healthineers) =======================
# Sites carrieres Phenom : widget JSON « refineSearch » public. Pas de description
# complete dans la liste -> accroche + competences detectees (ml_skills).
PHENOM_GROUPES = [   # (nom, base, refNum, lang, pays du site, valeur filtre France)
    ("Allianz", "https://careers.allianz.com", "AISAIPGB", "en_global", "global", "France"),
    ("Siemens Healthineers", "https://careers.siemens-healthineers.com", "SHDSHWUS",
     "en_global", "global", "France"),                  # Tours, Bourges
]

def rechercher_phenom(config, nom, base, ref, lang, pays, valeur_france):
    """Offres France d'un site carriere Phenom (entreprise = « Entite (Groupe) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    offres, depart, taille = [], 0, 50
    while len(offres) < config["max_offres"]:
        corps = {"lang": lang, "deviceType": "desktop", "country": pays,
                 "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
                 "subsearch": "", "from": depart, "jobs": True, "counts": True,
                 "all_fields": ["country"], "size": taille, "clearAll": False,
                 "jdsource": "facets", "isSliderEnable": False, "siteType": "external",
                 "keywords": mots_cles_recherche(config["mots_cles"]), "global": True,
                 "selected_fields": {"country": [valeur_france]}, "locationData": {},
                 "refNum": ref}
        r = requests.post(base + "/widgets", json=corps, timeout=25,
                          headers={"User-Agent": _UA_NAVIGATEUR,
                                   "Content-Type": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"{nom} {r.status_code}")
        res = r.json().get("refineSearch") or {}
        lot = (res.get("data") or {}).get("jobs") or []
        _compter_brut(nom, len(lot))
        for j in lot:
            contrat = " ".join(filter(None, [j.get("contractType"), j.get("type")]))
            publie = _iso(j.get("postedDate"))
            if limite and publie and publie[:10] < limite:
                continue
            if not _titre_ok(j.get("title", ""), inclus, exclus, contrat):
                continue
            cp = re.search(r"\b(\d{5})\b", j.get("address") or "")
            ville = (j.get("city") or "").title()
            competences = ", ".join(j.get("ml_skills") or [])
            offres.append({
                "intitule": j.get("title", ""),
                "entreprise": {"nom": _nom_groupe(j.get("company"), nom)},
                "lieuTravail": {"libelle": ", ".join(filter(None, [
                    ville, cp.group(1) if cp else "", "France"]))},
                "typeContratLibelle": contrat,
                "typeContrat": "CDI" if j.get("contractType") == "CDI" else "",
                "dateCreation": publie,
                "description": (j.get("descriptionTeaser") or "")
                               + (f"\nCompétences : {competences}" if competences else ""),
                # Page publique de l'offre : /{pays}/{langue}/job/{id} (« fr_fr » ->
                # /fr/fr/, « en_global » -> /global/en/) ; applyUrl est parfois vide.
                "origineOffre": {"urlOrigine": f"{base}/{lang.split('_')[1]}/"
                                 f"{lang.split('_')[0]}/job/{j['jobId']}" if j.get("jobId")
                                 else j.get("applyUrl") or ""},
                "salaire": {"libelle": j.get("salary", "") or ""},
                "_source": nom,
            })
        depart += taille
        if not lot or depart >= (res.get("totalHits") or 0):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 15. SOURCE CREDIT AGRICOLE (LCL, CA-CIB, Amundi...) =====================
# Site groupecreditagricole.jobs (WordPress) : endpoint admin-ajax « get_offers »,
# protege par un jeton (nonce) public lu dans la page de recherche.
CA_PAGE_URL = "https://groupecreditagricole.jobs/fr/nos-offres/"
CA_AJAX_URL = "https://groupecreditagricole.jobs/wp-admin/admin-ajax.php"
CA_FRANCE = "79"            # code pays « France » du site

def rechercher_credit_agricole(config):
    """Offres France du groupe Credit Agricole (entreprise = « Marque (Credit Agricole) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    s = requests.Session()
    s.headers["User-Agent"] = _UA_NAVIGATEUR
    m = re.search(r'"nonce":"([0-9a-f]+)"', s.get(CA_PAGE_URL, timeout=60).text)
    if not m:
        raise RuntimeError("Crédit Agricole : jeton (nonce) introuvable")
    offres, page = [], 1
    while len(offres) < config["max_offres"]:
        r = s.post(CA_AJAX_URL, timeout=60, data={
            "action": "get_offers", "nonce": m.group(1), "lang": "fr", "id_origine": "",
            "locationsList": CA_FRANCE, "page": page,
            "keywords": mots_cles_recherche(config["mots_cles"])})
        if r.status_code != 200:
            raise RuntimeError(f"Crédit Agricole {r.status_code}")
        data = r.json()
        lot = data.get("elements") or []
        _compter_brut("Crédit Agricole", len(lot))
        for e in lot:
            contrat = (e.get("contract") or {}).get("label", "") or ""
            publie = (e.get("date") or "")[:10]
            if limite and publie and publie < limite:
                continue
            if not _titre_ok(e.get("title", ""), inclus, exclus, contrat):
                continue
            offres.append({
                "intitule": e.get("title", ""),
                "entreprise": {"nom": _nom_groupe(e.get("brand_name"), "Crédit Agricole")},
                "lieuTravail": {"libelle": (e.get("location") or "France").replace(" - ", ", ")},
                "typeContratLibelle": contrat,
                "typeContrat": "CDI" if contrat == "CDI" else "",
                "dateCreation": publie,
                "description": "",          # absente de la liste (page HTML seulement)
                "origineOffre": {"urlOrigine": e.get("link", "")},
                "salaire": {"libelle": ""},
                "_source": "Crédit Agricole",
            })
        page += 1
        if not lot or not data.get("hasMore"):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === Fiche schema.org « JobPosting » (JSON-LD) d'une page d'offre ============
# Standard publie par la plupart des sites carrieres (pour Google Jobs) : date,
# employeur, lieu, description, type de contrat. Sert quand la LISTE est pauvre.
_RE_JSON_LD = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)

def _fiche_jobposting(url):
    """Bloc JobPosting (dict) de la page `url`, ou {} si absent / page indisponible."""
    try:
        r = _get_avec_retries(url, None, timeout=25, headers={"User-Agent": _UA_NAVIGATEUR})
    except RuntimeError:
        return {}
    if r.status_code != 200:
        return {}
    for bloc in _RE_JSON_LD.findall(r.text):
        try:
            d = json.loads(bloc.strip())
        except ValueError:
            continue
        candidats = d if isinstance(d, list) else (d.get("@graph") or [d]) if isinstance(d, dict) else []
        for x in candidats:
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    # Repli : microdonnees (itemprop="description", ex. SAP SuccessFactors) -> on
    # extrait chaque element en suivant l'imbrication des <span>/<div> et on garde
    # le plus long (la page en contient plusieurs, dont des quasi vides).
    meilleur = ""
    for m in re.finditer(r'<(span|div)\b[^>]*itemprop="description"[^>]*>', r.text):
        profondeur, fin = 1, len(r.text)
        for t in re.finditer(r"<(/?)(span|div)\b[^>]*?(/?)>", r.text[m.end():]):
            if t.group(3):                         # <span ... /> : sans contenu
                continue
            profondeur += -1 if t.group(1) else 1
            if profondeur == 0:
                fin = m.end() + t.start()
                break
        if fin - m.end() > len(meilleur):
            meilleur = r.text[m.end():fin]
    if not meilleur:
        return {}
    # Autres microdonnees courantes (<meta itemprop="…" content="…">) : date, lieu,
    # employeur -> meme forme que le JSON-LD pour _lieu_jobposting & co.
    meta = dict(re.findall(r'itemprop="([A-Za-z]+)"\s+content="([^"]*)"', r.text))
    fiche = {"description": meilleur,
             "datePosted": _date_libre(meta.get("datePosted", "")),
             "hiringOrganization": {"name": html.unescape(meta.get("hiringOrganization", ""))}}
    if meta.get("addressLocality"):
        fiche["jobLocation"] = {"address": {"addressLocality": html.unescape(meta["addressLocality"]),
                                            "postalCode": meta.get("postalCode", "")}}
    return fiche

# Mois (FR/EN, sans accents) -> numero, pour les dates « Oct 8, 2026 », « 8 oct. 2026 »,
# « Thu Oct 01 02:00:00 UTC 2026 ».
_MOIS = {"jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "avr": 4, "may": 5, "mai": 5,
         "aug": 8, "aou": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

def _date_libre(s):
    """Date ISO (AAAA-MM-JJ) depuis un texte de date libre FR/EN, ou ""."""
    s = normaliser(s or "")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    an = re.search(r"\b(20\d{2})\b", s)
    mois = None
    for mot in re.findall(r"[a-z]+", s):
        if mot.startswith(("juil", "jul")):
            mois = 7
        elif mot.startswith(("juin", "jun")):
            mois = 6
        else:
            mois = _MOIS.get(mot[:3])
        if mois:
            break
    reste = s.replace(an.group(1), " ") if an else s
    jour = re.search(r"(?<![\d:])(\d{1,2})(?![\d:])", reste)
    if not (an and mois and jour):
        return ""
    return f"{an.group(1)}-{mois:02d}-{int(jour.group(1)):02d}"

def _lieu_jobposting(fiche, defaut=""):
    """« Ville (CP), France » depuis jobLocation (dict ou liste) d'une fiche JobPosting.

    Certains sites mettent l'adresse complete dans addressLocality (« 1973 Bd de la
    Defense, 92000 Nanterre, France ») : on en extrait « CP Ville ». Jamais de numero
    de rue en tete (il serait pris pour un code departement).
    """
    loc = fiche.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    adr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    txt = adr.get("addressLocality") or adr.get("streetAddress") or ""
    cp = adr.get("postalCode") or ""
    m = re.search(r"\b(\d{5})\s+([^,\d]+)", txt)
    if m:
        cp, ville = m.group(1), m.group(2).strip()
    else:
        parts = [p.strip() for p in txt.split(",")
                 if p.strip() and normaliser(p.strip()) != "france"]
        ville = re.sub(r"^\d+\s*", "", parts[-1]) if parts else ""
    if not ville:
        return defaut or "France"
    return f"{ville} ({cp}), France" if cp else f"{ville}, France"

def _employeur_jobposting(fiche):
    org = fiche.get("hiringOrganization") or {}
    return org.get("name", "") if isinstance(org, dict) else str(org)

# === 16. SOURCE ENGIE (SAP SuccessFactors) ===================================
ENGIE_URL = "https://jobs.engie.com"

def rechercher_engie(config):
    """Offres France d'Engie (entreprise = « Entite juridique (Engie) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    s = requests.Session()
    s.headers["User-Agent"] = _UA_NAVIGATEUR
    offres, page, pages_vides = [], 0, 0
    while len(offres) < config["max_offres"] and page < 20:
        corps = {"locale": "fr_FR", "pageNumber": page, "sortBy": "", "location": "France",
                 "keywords": mots_cles_recherche(config["mots_cles"]), "facetFilters": {},
                 "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
        r = s.post(ENGIE_URL + "/services/recruiting/v1/jobs", json=corps, timeout=30,
                   headers={"Content-Type": "application/json", "Accept": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"Engie {r.status_code}")
        data = r.json()
        lot = data.get("jobSearchResult") or []
        _compter_brut("Engie", len(lot))
        pertinents = 0
        for x in lot:
            j = x.get("response") or {}
            titre = j.get("unifiedStandardTitle") or ""
            if inclus and not any(t in normaliser(titre) for t in inclus):
                continue
            pertinents += 1
            contrat = ", ".join(j.get("filter8") or [])
            m = re.match(r"(\d{2})/(\d{2})/(\d{4})", j.get("unifiedStandardStart") or "")
            publie = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""   # JJ/MM/AAAA
            if limite and publie and publie < limite:
                continue
            if not _titre_ok(titre, (), exclus, contrat):
                continue
            # unifiedUrlTitle est parfois deja encode (« %28FH%29 ») : on garde les %.
            url = (f"{ENGIE_URL}/job/"
                   f"{quote(j.get('unifiedUrlTitle') or j.get('urlTitle') or 'offre', safe='%-')}"
                   f"/{j.get('id')}-fr_FR")
            fiche = _fiche_jobposting(url)
            offres.append({
                "intitule": titre,
                "entreprise": {"nom": _nom_groupe((j.get("legalEntity_obj") or [""])[0], "Engie")},
                "lieuTravail": {"libelle": ((j.get("jobLocationShort") or [""])[0].strip()
                                            or "France")},
                "typeContratLibelle": contrat,
                "typeContrat": "CDI" if contrat == "CDI" else "",
                "dateCreation": publie,
                "description": _html_vers_texte(html.unescape(fiche.get("description") or "")),
                "origineOffre": {"urlOrigine": url},
                "salaire": {"libelle": ""},
                "_source": "Engie",
            })
        page += 1
        pages_vides = 0 if pertinents else pages_vides + 1   # recherche large : cf. Workday
        if not lot or page * len(lot) >= (data.get("totalJobs") or 0) \
                or (inclus and pages_vides >= 2):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 17. SOURCE RADANCY (Vinci, Veolia, L'Oreal) =============================
# Sites carrieres « TalentBrew » : la recherche renvoie du HTML (titre + lien) ;
# date, employeur, lieu et description viennent de la fiche JSON-LD de l'offre.
RADANCY_GROUPES = [
    ("Vinci", "https://jobs.vinci.com/en"),
    ("Veolia", "https://jobs.veolia.com/fr"),
    ("L'Oréal", "https://careers.loreal.com/en"),
]
RADANCY_FRANCE = "3017382"           # id GeoNames de la France (facette pays)
_RE_RADANCY_LIEN = re.compile(
    r'<a\b[^>]*?href="(/[^"]+)"[^>]*?data-job-id="(\d+)"[^>]*>(.*?)</a>', re.S)
_RE_RADANCY_TITRE = re.compile(
    r'<h\d[^>]*>(.*?)</h\d>|<span[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</span>', re.S)

def rechercher_radancy(config, nom, base):
    """Offres France d'un site carriere Radancy (entreprise = « Filiale (Groupe) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    hote = re.match(r"https://[^/]+", base).group(0)
    entetes = {"User-Agent": _UA_NAVIGATEUR, "X-Requested-With": "XMLHttpRequest",
               "Accept": "application/json, text/javascript, */*; q=0.01"}
    offres, page, pages_vides, vus = [], 1, 0, set()
    while len(offres) < config["max_offres"] and page <= 10:
        params = {"CurrentPage": page, "RecordsPerPage": 50, "SearchType": 5,
                  "Keywords": mots_cles_recherche(config["mots_cles"]),
                  "SearchResultsModuleName": "Search Results",
                  "SearchFiltersModuleName": "Search Filters",
                  "FacetFilters[0].ID": RADANCY_FRANCE, "FacetFilters[0].FacetType": 2,
                  "FacetFilters[0].Display": "France", "FacetFilters[0].IsApplied": "true"}
        r = _get_avec_retries(base + "/search-jobs/results", params, timeout=30, headers=entetes)
        if r.status_code != 200:
            raise RuntimeError(f"{nom} {r.status_code}")
        res = r.json().get("results") or ""
        pages = re.search(r'data-total-pages="(\d+)"', res)
        pertinents, nouveaux = 0, 0
        liens = _RE_RADANCY_LIEN.findall(res)
        _compter_brut(nom, len(liens))
        annonce = re.search(r'data-total-job-results="(\d+)"', res)
        if page == 1 and not liens and annonce and int(annonce.group(1)) > 0:
            raise RuntimeError(f"{nom} : {annonce.group(1)} offres annoncées mais "
                               "illisibles (structure HTML modifiée ?)")
        for chemin, ident, interieur in liens:
            if ident in vus:                       # meme offre : plusieurs liens
                continue
            vus.add(ident)
            nouveaux += 1
            mt = _RE_RADANCY_TITRE.search(interieur)
            titre = " ".join(html.unescape(re.sub(r"<[^>]+>", " ",
                             (mt.group(1) or mt.group(2)) if mt else interieur)).split())
            if inclus and not any(t in normaliser(titre) for t in inclus):
                continue
            pertinents += 1
            if not _titre_ok(titre, (), exclus):
                continue
            url = hote + chemin
            fiche = _fiche_jobposting(url)
            contrat = fiche.get("employmentType") or ""
            contrat = ", ".join(contrat) if isinstance(contrat, list) else str(contrat)
            publie = (fiche.get("datePosted") or "")[:10]
            if (limite and publie and publie < limite) or _RE_HORS_CDI.search(normaliser(contrat)):
                continue
            offres.append({
                "intitule": titre,
                "entreprise": {"nom": _nom_groupe(_employeur_jobposting(fiche), nom)},
                "lieuTravail": {"libelle": _lieu_jobposting(fiche)},
                "typeContratLibelle": contrat,
                "typeContrat": "",
                "dateCreation": publie,
                "description": _html_vers_texte(html.unescape(fiche.get("description") or "")),
                "origineOffre": {"urlOrigine": url},
                "salaire": {"libelle": ""},
                "_source": nom,
            })
        page += 1
        pages_vides = 0 if pertinents else pages_vides + 1
        if not nouveaux or page > int(pages.group(1) if pages else 1) \
                or (inclus and pages_vides >= 2):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 18. SOURCE DIGITALRECRUITERS (Cegedim) ==================================
# Sites carrieres DigitalRecruiters : liste JSON publique (recherche par mots-cles)
# + fiche JSON-LD de chaque offre. Offre en France = code postal dans l'URL.
DIGITALRECRUITERS_URL = "https://api.digitalrecruiters.com/public/v1/careers-site/job-ads"
DIGITALRECRUITERS_GROUPES = [        # (nom affiche, domaine du site carriere)
    ("Cegedim", "careers.cegedim.com"),
]

def rechercher_digitalrecruiters(config, nom, domaine):
    """Offres France d'un site DigitalRecruiters (entreprise = nom)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    entetes = {"User-Agent": _UA_NAVIGATEUR, "Content-Type": "application/json",
               "Accept": "application/json", "Origin": f"https://{domaine}",
               "Referer": f"https://{domaine}/"}
    offres, page, par_page, pages_vides = [], 1, 50, 0
    while len(offres) < config["max_offres"] and page <= 10:
        r = requests.post(DIGITALRECRUITERS_URL, timeout=30, headers=entetes,
                          params={"domainName": domaine, "limit": par_page,
                                  "page": page, "locale": "fr_FR"},
                          json={"q": mots_cles_recherche(config["mots_cles"])})
        if r.status_code != 200:
            raise RuntimeError(f"{nom} {r.status_code}")
        data = r.json()
        lot = data.get("items") or []
        _compter_brut(nom, len(lot))
        pertinents = 0
        for it in lot:
            titre = it.get("title", "") or ""
            if inclus and not any(t in normaliser(titre) for t in inclus):
                continue
            pertinents += 1
            cp = re.search(r"-(\d{5})-[a-z0-9-]+$", it.get("url", "") or "")
            if not cp:                              # pas de code postal -> hors France
                continue
            if not _titre_ok(titre, (), exclus, it.get("contract", "")):
                continue
            url = f"https://{domaine}/fr/annonce/{it.get('url', '')}"
            fiche = _fiche_jobposting(url)
            publie = (fiche.get("datePosted") or "")[:10]
            if limite and publie and publie < limite:
                continue
            ville = it.get("location") or ""
            offres.append({
                "intitule": titre,
                "entreprise": {"nom": nom},
                "lieuTravail": {"libelle": f"{ville} ({cp.group(1)}), France" if ville
                                else _lieu_jobposting(fiche)},
                "typeContratLibelle": it.get("contract", "") or "",
                "typeContrat": "CDI" if it.get("contract") == "CDI" else "",
                "dateCreation": publie,
                "description": _html_vers_texte(html.unescape(fiche.get("description") or "")),
                "origineOffre": {"urlOrigine": url},
                "salaire": {"libelle": ""},
                "_source": nom,
            })
        page += 1
        pages_vides = 0 if pertinents else pages_vides + 1   # tri par score de pertinence
        if not lot or (page - 1) * par_page >= (data.get("count") or 0) \
                or (inclus and pages_vides >= 2):
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 19. SOURCE SUCCESSFACTORS « RMK » (pages HTML) ==========================
# Sites carrieres SAP SuccessFactors classiques : l'API JSON y est fermee (401),
# seule la page de recherche HTML repond (tableau ou tuiles). Fiche de l'offre en
# microdonnees (description, et selon le site date / lieu / entite).
RMK_GROUPES = [   # Centre-Val de Loire : Servier (Gidy), Novo Nordisk (Chartres), Shiseido (Ormes, Gien)
    ("Servier", "https://jobs.servier.com"),
    ("Novo Nordisk", "https://careers.novonordisk.com"),
    ("Shiseido", "https://careers.shiseido.com"),
    ("Guerbet", "https://careers.guerbet.com"),
    ("Coty", "https://careers.coty.com"),
    ("Boehringer Ingelheim", "https://jobs.boehringer-ingelheim.com"),
]
_RE_RMK_LIGNE = re.compile(r'<tr class="data-row[^"]*">(.*?)</tr>', re.S)
_RE_RMK_LIEN = re.compile(r'<a\b[^>]*?href="(/job/[^"]+)"[^>]*>\s*([^<]*?)\s*</a>', re.S)
_RE_RMK_LIEU = re.compile(r'class="(?:jobLocation|jobFacility)"[^>]*>\s*([^<]+?)\s*<'
                          r'|(?:id|class)="[^"]*section-location-value[^"]*"[^>]*>\s*([^<]+?)\s*<')
_RE_RMK_DATE = re.compile(r'class="jobDate[^"]*"[^>]*>\s*([^<]+?)\s*<')

def rechercher_successfactors_html(config, nom, base):
    """Offres France d'un site SuccessFactors « RMK » (entreprise = « Entite (Groupe) »)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    entetes = {"User-Agent": _UA_NAVIGATEUR, "Accept-Language": "fr-FR,fr;q=0.9"}
    offres, debut, vus = [], 0, set()
    while len(offres) < config["max_offres"] and debut < 500:
        r = _get_avec_retries(base + "/search/", {"q": mots_cles_recherche(config["mots_cles"]),
                                                  "locationsearch": "France", "startrow": debut},
                              timeout=30, headers=entetes)
        if r.status_code != 200:
            raise RuntimeError(f"{nom} {r.status_code}")
        t = r.text
        blocs = _RE_RMK_LIGNE.findall(t) or re.split(r'<li class="job-tile ', t)[1:]
        _compter_brut(nom, len(blocs))
        if not blocs and "jobTitle-link" in t:      # offres presentes mais illisibles
            raise RuntimeError(f"{nom} : offres annoncées mais illisibles "
                               "(structure HTML modifiée ?)")
        nouveaux = 0
        for bloc in blocs:
            lien = _RE_RMK_LIEN.search(bloc)
            if not lien or lien.group(1) in vus:
                continue
            vus.add(lien.group(1))
            nouveaux += 1
            titre = html.unescape(" ".join(lien.group(2).split()))
            if not _titre_ok(titre, inclus, exclus):
                continue
            lieu_liste = _RE_RMK_LIEU.search(bloc)
            lieu_liste = html.unescape(lieu_liste.group(1) or lieu_liste.group(2)) if lieu_liste else ""
            date_liste = _RE_RMK_DATE.search(bloc)
            url = base + lien.group(1)
            fiche = _fiche_jobposting(url)
            publie = fiche.get("datePosted") or (_date_libre(date_liste.group(1)) if date_liste else "")
            if limite and publie and publie < limite:
                continue
            offres.append({
                "intitule": titre,
                "entreprise": {"nom": _nom_groupe(_employeur_jobposting(fiche).title(), nom)},
                "lieuTravail": {"libelle": _lieu_jobposting(fiche, lieu_liste or "France")},
                "typeContratLibelle": "",
                "typeContrat": "",
                "dateCreation": publie,
                "description": _html_vers_texte(fiche.get("description") or ""),
                "origineOffre": {"urlOrigine": url},
                "salaire": {"libelle": ""},
                "_source": nom,
            })
        debut += len(blocs)
        if not blocs or not nouveaux:
            break
        time.sleep(0.2)
    return offres[: config["max_offres"]]

# === 20. SOURCE ASHBY (e-sante : Doctolib, Alan, Lifen, Owkin, Nabla, Inato) ==
# API publique Ashby : TOUT le catalogue d'un coup (pas de recherche) -> cache 30 min
# par organisation, puis tri sur le titre ; offres France = adresse / lieu en France.
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}"
ASHBY_GROUPES = [    # (nom affiche, identifiant Ashby)
    ("Doctolib", "doctolib"), ("Alan SA", "alan"), ("Lifen", "lifen"),
    ("Owkin", "owkin"), ("Nabla", "nabla"), ("Inato", "inato"),
]
_ashby_cache = {}    # org -> (horodatage, offres)

def _ashby_catalogue(org):
    if org not in _ashby_cache or time.time() - _ashby_cache[org][0] > 1800:
        r = _get_avec_retries(ASHBY_URL.format(org=org), None, timeout=30,
                              headers={"User-Agent": _UA_NAVIGATEUR})
        if r.status_code != 200:
            raise RuntimeError(f"Ashby {org} {r.status_code}")
        _ashby_cache[org] = (time.time(), r.json().get("jobs") or [])
    return _ashby_cache[org][1]

def rechercher_ashby(config, nom, org):
    """Offres France d'une organisation Ashby (entreprise = nom)."""
    inclus, exclus = _prefiltre_titres(config)
    limite = _limite_iso(config)
    offres = []
    for x in _ashby_catalogue(org):
        if x.get("isListed") is False:
            continue
        adr = (x.get("address") or {}).get("postalAddress") or {}
        lieux = " ".join([x.get("location") or "", adr.get("addressCountry") or ""]
                         + [str((s or {}).get("location") or "")
                            for s in x.get("secondaryLocations") or []])
        if not re.search(r"\bfrance\b|\bparis\b", normaliser(lieux)):
            continue
        _compter_brut(nom, 1)
        publie = (x.get("publishedAt") or "")[:10]
        if limite and publie and publie < limite:
            continue
        if not _titre_ok(x.get("title", ""), inclus, exclus, x.get("employmentType") or ""):
            continue
        ville = adr.get("addressLocality") or x.get("location") or ""
        cp = adr.get("postalCode") or ""
        offres.append({
            "intitule": x.get("title", ""),
            "entreprise": {"nom": nom},
            "lieuTravail": {"libelle": f"{ville} ({cp}), France" if cp
                            else ville if "france" in normaliser(ville) else f"{ville}, France"},
            "typeContratLibelle": x.get("employmentType") or "",
            "typeContrat": "",
            "dateCreation": x.get("publishedAt") or "",
            "description": x.get("descriptionPlain") or _html_vers_texte(x.get("descriptionHtml")),
            "origineOffre": {"urlOrigine": x.get("jobUrl", "")},
            "salaire": {"libelle": ""},
            "_source": "Ashby",
        })
    return offres[: config["max_offres"]]

# === 21. SITES CARRIERES : toutes sources, en parallele ======================
def sites_carrieres():
    """[(nom, fonction(config) -> offres)] : toutes les sources « sites carrieres ».

    nom = nom d'entreprise, le meme que celui passe a _compter_brut par le connecteur."""
    sites = [(g[0],
              lambda cfg, g=g: rechercher_workday(
                  cfg, g[0], g[1], g[2] if len(g) > 2 else "locationCountry"))
             for g in WORKDAY_GROUPES]
    sites += [(nom, lambda cfg, n=nom, b=base: rechercher_jibe(cfg, n, b))
              for nom, base in JIBE_GROUPES]
    sites += [(nom, lambda cfg, n=nom, i=i: rechercher_smartrecruiters(cfg, n, i))
              for nom, i in SMARTRECRUITERS_GROUPES]
    sites += [(g[0], lambda cfg, g=g: rechercher_phenom(cfg, *g)) for g in PHENOM_GROUPES]
    sites += [(nom, lambda cfg, n=nom, b=base: rechercher_radancy(cfg, n, b))
              for nom, base in RADANCY_GROUPES]
    sites += [(nom, lambda cfg, n=nom, d=dom: rechercher_digitalrecruiters(cfg, n, d))
              for nom, dom in DIGITALRECRUITERS_GROUPES]
    sites += [(nom, lambda cfg, n=nom, b=base: rechercher_successfactors_html(cfg, n, b))
              for nom, base in RMK_GROUPES]
    sites += [(nom, lambda cfg, n=nom, o=org: rechercher_ashby(cfg, n, o))
              for nom, org in ASHBY_GROUPES]
    return sites + [("LVMH", rechercher_lvmh), ("BPCE", rechercher_bpce),
                    ("Crédit Agricole", rechercher_credit_agricole),
                    ("Engie", rechercher_engie)]

def rechercher_sites_carrieres(config, variantes, workers=16):
    """Interroge chaque site carriere pour chaque variante, EN PARALLELE.

    Sequentiel, ~30 sites x 7 variantes prendrait ~15 min (minutes GitHub Actions
    comptees, depot prive) ; en parallele ~1-2 min. -> (offres, erreurs), chaque
    offre marquee _variante. Une erreur sur un site n'arrete pas les autres.
    """
    sites = sites_carrieres()
    with _volumes_verrou:                    # volumes bruts de CE passage (voir volumes_bruts)
        _volumes.clear()
        _volumes.update({nom: 0 for nom, _fn in sites})
    taches = [(v, nom, fn) for v in variantes for nom, fn in sites]
    offres, erreurs = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futurs = [ex.submit(fn, dict(config, mots_cles=v)) for v, _nom, fn in taches]
        for (v, nom, _fn), fut in zip(taches, futurs):
            try:
                for o in fut.result():
                    o["_variante"], o["_site"] = v, nom
                    offres.append(o)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"{nom} « {v} » : {e}")
    # Fiches d'offres toutes vides sur un site : page d'offre modifiee (JSON-LD,
    # microdonnees…) -> alerte plutot qu'une perte silencieuse de description.
    par_site = {}
    for o in offres:
        par_site.setdefault(o["_site"], []).append(bool(o.get("description")))
    for nom, descs in par_site.items():
        if nom not in _SITES_SANS_DESCRIPTION and len(descs) >= 3 and not any(descs):
            erreurs.append(f"{nom} : fiches d'offres illisibles (description vide) — "
                           "structure modifiée ?")
    return offres, erreurs
