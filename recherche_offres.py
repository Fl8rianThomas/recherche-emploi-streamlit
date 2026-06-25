#!/usr/bin/env python3
"""Recherche d'offres France Travail avec exclusion des ESN + dedoublonnage."""

import os
import re
import csv
import sys
import time
import unicodedata
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

# === 1. CRITERES DE RECHERCHE ===============================================
CONFIG = {
    "mots_cles": "business analyst",
    "departement": None,          # "75", "69"... ou None pour toute la France
    "commune": None,              # code INSEE (pas le code postal)
    "distance": 30,
    "type_contrat": "CDI",        # "CDI", "CDD", "MIS", ou None
    "jours_max": None,            # ancienneté max en jours (None = pas de limite)
    "max_offres": 150,
    "fichier_csv": "offres_filtrees.csv",
    "fichier_exclues": "offres_exclues.csv",
}

EXCLURE_PAR_TEXTE = True
EXCLURE_AGENCES   = False
DEDOUBLONNER      = True
EXCLURE_ANONYMES  = False

# Variantes de metier : chaque ligne declenche une recherche, puis tout est
# fusionne et dedoublonne. Permet de capter les offres dont le titre utilise
# un synonyme (ex: "analyste d'affaires" au lieu de "business analyst").
SYNONYMES_PAR_DEFAUT = [
    "business analyst",
    "analyste d'affaires",
    "analyste métier",
    "analyste fonctionnel",
    "AMOA",
]

# Le TITRE doit contenir au moins un de ces mots (sinon l'offre est ecartee).
# Vise le coeur du metier : evite le bruit capte via la description seule
# (developpeurs, techniciens...). Editable / desactivable dans l'interface.
INCLUSIONS_TITRE_PAR_DEFAUT = [
    "analyst",      # couvre analyst + analyste
    "business",
    "amoa",
    "fonctionnel",
]

# Mots dont la presence dans le TITRE exclut l'offre (metiers voisins / hors sujet).
# Editable dans l'interface.
EXCLUSIONS_TITRE_PAR_DEFAUT = [
    "consultant",
    "eaux usees",
    "assainissement",
    "reseaux humides",
    "affaires reglementaires",
]

# Profil "Optimum" : competences / domaines servant a classer les offres par
# adequation. Format "terme" (poids 1) ou "terme:poids". Les DOMAINES d'experience
# (RH, mutuelle, assurance, banque...) sont volontairement pondered plus fort.
# Editable dans l'interface.
PROFIL_OPTIMUM = [
    # Domaine RH (coeur d'expertise) - poids tres fort
    "ressources humaines:5", "rh:5", "sirh:5", "human resources:5",
    "hris:5", "hcm:5", "gpec:5", "administration du personnel:5",
    "gestion du personnel:5", "talentsoft:5",
    "gta:5",
    # Autres domaines / secteurs d'experience
    "mutuelle:3", "assurance:3", "protection sociale:3", "prevoyance:3",
    "banque:3", "force de vente:2", "commercial:2",
    # Metier / methodes (poids moyen)
    "amoa:2", "analyse fonctionnelle:2", "maitrise d'ouvrage:2",
    "specifications fonctionnelles:1", "regles de gestion:1", "bpmn:1",
    "modelisation:1", "processus:1", "product owner:1",
    # Outils & agile (poids leger)
    "sql:1", "figma:1", "jira:1", "confluence:1", "salesforce:1",
    "agile:1", "scrum:1", "kanban:1", "psm:1", "backlog:1",
]

# Competences relevant du domaine RH -> declenchent l'icone 👥 dans l'interface.
COMPETENCES_RH = {
    "ressources humaines", "rh", "sirh", "human resources", "hris", "hcm",
    "gpec", "administration du personnel", "gestion du personnel",
    "talentsoft", "gta",
}

def competences_rh(competences):
    """Sous-ensemble des competences trouvees qui relevent du domaine RH."""
    return [c for c in competences if normaliser(c) in COMPETENCES_RH]

def comprend_rh(competences):
    """True si au moins une competence trouvee releve du domaine RH."""
    return bool(competences_rh(competences))

# === 2. LISTES D'EXCLUSION ==================================================
ESN_CONNUES = {
    "capgemini", "sopra steria", "atos", "cgi", "alten", "altran",
    "akkodis", "akka", "modis", "devoteam", "inetum", "gfi", "sogeti",
    "expleo", "assystem", "econocom", "sword", "talan", "umanis", "hardis",
    "keyrus", "micropole", "business & decision", "onepoint", "wavestone",
    "niji", "davidson", "mc2i", "ausy", "neurones", "groupe open",
    "sii", "societe pour l informatique industrielle", "alithya", "astek",
    "segula", "aubay", "infotel", "solutec", "linkbynet", "smile", "norsys",
    "cellenza", "ippon", "octo", "zenika", "viseo", "saegus", "meritis",
    "scalian", "extia", "amaris", "mantu", "consort", "infosys", "wipro",
    "tata consultancy", "cognizant", "synapse", "positive thinking",
    "alteca", "seyos", "kapa it", "kapa", "geser best", "geser-best", "geser",
    "horisis", "horisis conseil", "brain logic", "ciorane", "alphavote",
    "bpce solutions informatiques", "viveris", "dcs easyware", "dcs group",
}

AGENCES_RECRUTEMENT = {
    "lynx rh", "michael page", "page personnel", "hays", "robert walters",
    "robert half", "fed it", "fed finance", "expectra", "randstad",
    "manpower", "adecco", "approach people", "urban linker", "externatic",
    "hunteed", "kelly services", "lincoln", "gi group", "morgan philips",
}

SIGNAUX_ESN = [
    "esn", "ssii", "societe de services", "societe de service", "regie",
    "notre client", "nos clients", "client final",
    "au sein de nos clients", "mission chez",
    "en prestation", "cabinet de conseil", "societe de conseil en informatique",
]

# Hubs / pays asiatiques (+ Moyen-Orient expat) qui declenchent l'onglet Asie.
# Une offre y entre si son texte EVOQUE l'un de ces lieux ET qu'elle est en
# français (signal d'une entreprise FR recrutant a l'etranger). Editable.
HUBS_ASIE = {
    "asie", "asia", "asie-pacifique", "asia-pacific", "apac",
    "singapour", "singapore", "hong kong", "dubai", "tokyo", "shanghai",
    "bangkok", "japon", "japan", "chine", "china", "thailande", "thailand",
    "vietnam", "hanoi", "ho chi minh", "malaisie", "malaysia", "kuala lumpur",
    "indonesie", "indonesia", "jakarta", "inde", "india", "bangalore",
    "mumbai", "seoul", "coree", "taiwan", "emirats", "emirates",
}

# Mots-outils français frequents : servent a deviner qu'une annonce est en
# français (et donc, sur un poste asiatique, cible un profil francophone).
MARQUEURS_FRANCAIS = {
    "le", "la", "les", "des", "une", "un", "et", "ou", "pour", "avec",
    "vous", "nous", "dans", "du", "au", "aux", "sur", "qui", "que", "est",
    "sont", "votre", "notre", "nos", "vos", "ses", "cette", "par", "ne",
    "pas", "plus", "sein", "afin", "ainsi", "chez", "sera", "etre",
}

# === 3. FILTRAGE ============================================================
APOSTROPHES = "’‘ʼ`´′"

def normaliser(t):
    if not t:
        return ""
    t = t.lower()
    for c in APOSTROPHES:
        t = t.replace(c, "'")
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")

def mots_cles_recherche(t):
    """Nettoie les mots-cles envoyes aux API : apostrophes -> espace.

    Indispensable pour Adzuna, qui renvoie 0 resultat des qu'une apostrophe est
    presente dans 'what' (ex: "charge d'affaires"). Sans effet negatif pour
    France Travail et Jooble.
    """
    if not t:
        return t
    for c in APOSTROPHES + "'":
        t = t.replace(c, " ")
    return re.sub(r"\s+", " ", t).strip()

def construire_regex(termes):
    termes = sorted({normaliser(x) for x in termes if x})
    return re.compile("|".join(r"\b" + re.escape(x) + r"\b" for x in termes))

REGEX_ESN = construire_regex(ESN_CONNUES)
REGEX_AGENCES = construire_regex(AGENCES_RECRUTEMENT)
REGEX_SIGNAUX = construire_regex(SIGNAUX_ESN)
REGEX_ASIE = construire_regex(HUBS_ASIE)

def mentionne_asie(o):
    """True si l'intitule / la description / le lieu evoque l'Asie ou un hub."""
    txt = (o.get("intitule", "") + " " + (o.get("description") or "") + " "
           + (o.get("lieuTravail") or {}).get("libelle", ""))
    return bool(REGEX_ASIE.search(normaliser(txt)))

def est_francais(texte, seuil=4):
    """Devine si un texte est redige en français (mots-outils FR distincts)."""
    mots = set(re.findall(r"[a-z]+", normaliser(texte)))
    return len(MARQUEURS_FRANCAIS & mots) >= seuil

def cible_asie(o):
    """Offre « France -> Asie » : annonce en français ciblant l'Asie.

    Heuristique pour reperer une entreprise FR recrutant un profil francophone
    a l'etranger : le texte evoque un hub asiatique ET il est redige en français.
    """
    if not mentionne_asie(o):
        return False
    return est_francais(o.get("intitule", "") + " " + (o.get("description") or ""))

def nom_entreprise(o):
    return o.get("entreprise", {}).get("nom", "").strip()

def classer(o):
    ent = normaliser(nom_entreprise(o))
    txt = normaliser(o.get("intitule", "") + " " + o.get("description", ""))
    if ent and REGEX_AGENCES.search(ent):
        # Cabinet de recrutement explicite : intermediaire legitime (souvent des
        # CDI chez le client final). On l'exclut seulement si EXCLURE_AGENCES ;
        # sinon on le CONSERVE en contournant les filtres ESN (nom) et signaux
        # texte ("notre client", "en prestation"...), qui ici sont des faux positifs.
        if EXCLURE_AGENCES:
            return False, "cabinet de recrutement"
        return True, ""
    if not ent:
        # Entreprise masquee : un nom d'ESN connue dans le texte trahit l'employeur
        # (ex: offre anonyme dont la description dit "Sopra Steria, acteur majeur...").
        if EXCLURE_PAR_TEXTE and REGEX_ESN.search(txt):
            return False, "ESN (texte)"
        if EXCLURE_ANONYMES:
            return False, "entreprise anonyme"
    else:
        if REGEX_ESN.search(ent):
            return False, "ESN (nom)"
    if EXCLURE_PAR_TEXTE and REGEX_SIGNAUX.search(txt):
        return False, "indice ESN dans le texte"
    return True, ""

def signature(o):
    titre = normaliser(o.get("intitule", ""))
    titre = re.sub(r"[\(\[]?\s*[hfmnd](\s*/\s*[hfmnd])+\s*[\)\]]?", " ", titre)
    titre = re.sub(r"\s+", " ", titre).strip()
    return titre + "||" + normaliser(nom_entreprise(o))

# === 4. API FRANCE TRAVAIL ==================================================
TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

def obtenir_token(cid, secret):
    r = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials", "client_id": cid,
        "client_secret": secret, "scope": SCOPE}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Code {r.status_code} - {r.text}")
    return r.json()["access_token"]

def rechercher(token, config):
    headers = {"Authorization": f"Bearer {token}"}
    base = {"motsCles": mots_cles_recherche(config["mots_cles"])}
    if config.get("departement"):
        base["departement"] = config["departement"]
    if config.get("commune"):
        base["commune"] = config["commune"]
        base["distance"] = config["distance"]
    if config.get("type_contrat"):
        base["typeContrat"] = config["type_contrat"]
    if config.get("jours_max"):
        mind = date.today() - timedelta(days=config["jours_max"])
        base["minCreationDate"] = mind.isoformat() + "T00:00:00Z"
        base["maxCreationDate"] = date.today().isoformat() + "T23:59:59Z"
    offres, taille, debut = [], 50, 0
    while len(offres) < config["max_offres"]:
        params = dict(base, range=f"{debut}-{debut + taille - 1}")
        r = requests.get(SEARCH_URL, headers=headers, params=params, timeout=20)
        if r.status_code not in (200, 206):
            print(f"  Erreur API {r.status_code} : {r.text[:200]}")
            break
        lot = r.json().get("resultats", [])
        if not lot:
            break
        offres.extend(lot)
        if len(lot) < taille:
            break
        debut += taille
        time.sleep(0.3)
    return offres[: config["max_offres"]]

# === 5. SORTIE ==============================================================
def ligne(o, raison=None):
    d = {
        "intitule": o.get("intitule", ""),
        "entreprise": nom_entreprise(o) or "Entreprise non precisee",
        "lieu": o.get("lieuTravail", {}).get("libelle", ""),
        "contrat": o.get("typeContratLibelle", o.get("typeContrat", "")),
        "date": o.get("dateCreation", ""),
        "url": o.get("origineOffre", {}).get("urlOrigine", ""),
    }
    if raison is not None:
        d["raison_exclusion"] = raison
    return d

def ecrire_csv(lignes, chemin, champs):
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(lignes)

# Code departement -> chef-lieu (pour traduire entre France Travail [code] et
# Adzuna/Jooble [nom de lieu]). Permet un champ "Lieu" unique dans l'interface.
DEPARTEMENTS = {
    "01": "Bourg-en-Bresse", "02": "Laon", "03": "Moulins", "04": "Digne-les-Bains",
    "05": "Gap", "06": "Nice", "07": "Privas", "08": "Charleville-Mézières",
    "09": "Foix", "10": "Troyes", "11": "Carcassonne", "12": "Rodez",
    "13": "Marseille", "14": "Caen", "15": "Aurillac", "16": "Angoulême",
    "17": "La Rochelle", "18": "Bourges", "19": "Tulle", "21": "Dijon",
    "22": "Saint-Brieuc", "23": "Guéret", "24": "Périgueux", "25": "Besançon",
    "26": "Valence", "27": "Évreux", "28": "Chartres", "29": "Quimper",
    "2A": "Ajaccio", "2B": "Bastia", "30": "Nîmes", "31": "Toulouse",
    "32": "Auch", "33": "Bordeaux", "34": "Montpellier", "35": "Rennes",
    "36": "Châteauroux", "37": "Tours", "38": "Grenoble", "39": "Lons-le-Saunier",
    "40": "Mont-de-Marsan", "41": "Blois", "42": "Saint-Étienne",
    "43": "Le Puy-en-Velay", "44": "Nantes", "45": "Orléans", "46": "Cahors",
    "47": "Agen", "48": "Mende", "49": "Angers", "50": "Saint-Lô",
    "51": "Châlons-en-Champagne", "52": "Chaumont", "53": "Laval", "54": "Nancy",
    "55": "Bar-le-Duc", "56": "Vannes", "57": "Metz", "58": "Nevers",
    "59": "Lille", "60": "Beauvais", "61": "Alençon", "62": "Arras",
    "63": "Clermont-Ferrand", "64": "Pau", "65": "Tarbes", "66": "Perpignan",
    "67": "Strasbourg", "68": "Colmar", "69": "Lyon", "70": "Vesoul",
    "71": "Mâcon", "72": "Le Mans", "73": "Chambéry", "74": "Annecy",
    "75": "Paris", "76": "Rouen", "77": "Melun", "78": "Versailles",
    "79": "Niort", "80": "Amiens", "81": "Albi", "82": "Montauban",
    "83": "Toulon", "84": "Avignon", "85": "La Roche-sur-Yon", "86": "Poitiers",
    "87": "Limoges", "88": "Épinal", "89": "Auxerre", "90": "Belfort",
    "91": "Évry", "92": "Nanterre", "93": "Bobigny", "94": "Créteil",
    "95": "Cergy", "971": "Basse-Terre", "972": "Fort-de-France",
    "973": "Cayenne", "974": "Saint-Denis", "976": "Mamoudzou",
}
_NOM_VERS_CODE = {normaliser(v): k for k, v in DEPARTEMENTS.items()}

def resoudre_lieu(saisie):
    """Traduit une saisie libre en (code_departement, nom_lieu).

    - "75" / "2A"  -> ("75", "Paris")        (code -> chef-lieu)
    - "Lyon"        -> ("69", "Lyon")          (ville connue -> code)
    - "Bruz"        -> (None, "Bruz")          (lieu non mappe : FT national, nom transmis)
    - ""            -> (None, None)
    """
    s = (saisie or "").strip()
    if not s:
        return None, None
    code = s.upper()
    if code in DEPARTEMENTS:
        return code, DEPARTEMENTS[code]
    code_depuis_nom = _NOM_VERS_CODE.get(normaliser(s))
    return code_depuis_nom, s

# Departement -> region (pour la repartition territoriale du tableau de bord).
_REGIONS_DEPTS = {
    "Auvergne-Rhône-Alpes": ["01","03","07","15","26","38","42","43","63","69","73","74"],
    "Bourgogne-Franche-Comté": ["21","25","39","58","70","71","89","90"],
    "Bretagne": ["22","29","35","56"],
    "Centre-Val de Loire": ["18","28","36","37","41","45"],
    "Corse": ["2A","2B"],
    "Grand Est": ["08","10","51","52","54","55","57","67","68","88"],
    "Hauts-de-France": ["02","59","60","62","80"],
    "Île-de-France": ["75","77","78","91","92","93","94","95"],
    "Normandie": ["14","27","50","61","76"],
    "Nouvelle-Aquitaine": ["16","17","19","23","24","33","40","47","64","79","86","87"],
    "Occitanie": ["09","11","12","30","31","32","34","46","48","65","66","81","82"],
    "Pays de la Loire": ["44","49","53","72","85"],
    "Provence-Alpes-Côte d'Azur": ["04","05","06","13","83","84"],
    "Outre-mer": ["971","972","973","974","976"],
}
DEPT_REGION = {c: reg for reg, codes in _REGIONS_DEPTS.items() for c in codes}

def region_offre(o):
    """Devine la region française d'une offre depuis son lieu. 'Autre' sinon."""
    lieu = (o.get("lieuTravail") or {}).get("libelle", "") or ""
    if not lieu.strip():
        return "Non précisé"
    m = re.match(r"\s*(2[AB]|\d{2,3})\b", lieu)           # "75 - Paris" (France Travail)
    if m:
        return DEPT_REGION.get(m.group(1).upper(), "Autre")
    norm = normaliser(lieu)
    for reg in _REGIONS_DEPTS:                             # nom de region dans le libelle (Adzuna)
        if normaliser(reg) in norm:
            return reg
    for part in re.split(r"[,/]", lieu):                  # ville chef-lieu -> code -> region
        code = _NOM_VERS_CODE.get(normaliser(part))
        if code:
            return DEPT_REGION.get(code, "Autre")
    return "Autre"

def date_offre(o):
    """Date de parution d'une offre (les 10 premiers caracteres ISO), ou None."""
    s = (o.get("dateCreation") or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None

# Detection France / International (le bruit US vient surtout de Jooble).
ETATS_US = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}
MARQUEURS_ETRANGERS = {
    # etats US (noms complets, normalises)
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut",
    "delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa",
    "kansas","kentucky","louisiana","maryland","massachusetts","michigan",
    "minnesota","mississippi","missouri","montana","nebraska","nevada",
    "new hampshire","new jersey","new mexico","new york","north carolina",
    "north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island",
    "south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","washington dc","west virginia","wisconsin","wyoming",
    # pays / divers
    "united states","united kingdom","remote","abroad","india","ireland","canada",
    "singapore","mexico","luxembourg","suisse","switzerland","geneve",
    "puerto rico","united arab emirates","qatar","japan",
}

def est_international(o):
    """True si l'offre est hors France (etat US, pays etranger...)."""
    lieu = (o.get("lieuTravail") or {}).get("libelle", "") or ""
    if not lieu.strip():
        return False  # lieu inconnu -> on suppose France
    m = re.search(r",\s*([A-Z]{2})\b", lieu)
    if m and m.group(1) in ETATS_US:
        return True
    for p in re.split(r"[,/]", lieu):
        n = normaliser(p)
        if n.endswith(" state"):        # "New York State" -> "new york"
            n = n[:-6].strip()
        if n in MARQUEURS_ETRANGERS:
            return True
    return False

def _competence_poids(brut):
    """'mutuelle:3' -> ('mutuelle', 3) ; 'sql' -> ('sql', 1)."""
    terme, sep, p = brut.rpartition(":")
    if sep and p.strip().isdigit():
        return terme.strip(), max(1, int(p))
    return brut.strip(), 1

def score_optimum(o, profil):
    """Adequation d'une offre avec un profil de competences pondere.

    Chaque ligne du profil vaut son poids (defaut 1, ou "terme:poids"). Une
    competence trouvee dans le titre compte double (signal plus fort que la
    description, souvent un simple extrait cote Adzuna/Jooble).
    Retourne (score, competences_trouvees) ou competences_trouvees liste les termes.
    """
    titre = normaliser(o.get("intitule", ""))
    txt = titre + " " + normaliser(o.get("description") or "")
    trouves = []
    score = 0
    for brut in profil:
        terme, poids = _competence_poids(brut)
        c = normaliser(terme)
        if not c:
            continue
        if c in titre:
            trouves.append(terme)
            score += poids * 2
        elif c in txt:
            trouves.append(terme)
            score += poids
    return score, trouves

def filtrer(brutes, jours_max=None, exclure_titres=None, inclure_titres=None,
            ignorer_esn=False):
    """Filtrage ESN + titre + ancienneté + dedoublonnage sur une liste d'offres.

    jours_max     : exclut les offres parues il y a plus de N jours (None = aucune
                    limite). Les offres sans date exploitable sont conservees.
    inclure_titres: si non vide, ne garde que les offres dont le titre contient
                    au moins un de ces mots (filtre de pertinence).
    exclure_titres: liste de mots qui, presents dans le titre, excluent l'offre.
    ignorer_esn   : si True, on saute la classification ESN/cabinet/anonyme
                    (classer) ; les autres filtres (titre, ancienneté, doublons)
                    s'appliquent toujours. Utile pour l'onglet Asie, ou une ESN
                    française recrutant a l'etranger reste une piste pertinente.

    Retourne (gardees, exclues, stats) ou :
      - gardees : offres brutes retenues
      - exclues : lignes (via ligne()) avec raison_exclusion
      - stats   : dict {brutes, gardees, exclues, doublons, compte}
    """
    limite = date.today() - timedelta(days=jours_max) if jours_max else None
    exclus = [normaliser(t) for t in (exclure_titres or []) if t.strip()]
    inclus = [normaliser(t) for t in (inclure_titres or []) if t.strip()]
    gardees, exclues, vues, compte, doublons = [], [], set(), {}, 0
    for o in brutes:
        garder, raison = (True, "") if ignorer_esn else classer(o)
        titre = normaliser(o.get("intitule", ""))
        if garder and inclus and not any(t in titre for t in inclus):
            garder, raison = False, "hors périmètre (titre)"
        if garder and exclus:
            for term in exclus:
                if term in titre:
                    garder, raison = False, f"titre exclu ({term})"
                    break
        if garder and limite is not None:
            d = date_offre(o)
            if d is not None and d < limite:
                garder, raison = False, f"trop ancienne (> {jours_max} j)"
        if not garder:
            exclues.append(ligne(o, raison))
            compte[raison] = compte.get(raison, 0) + 1
            continue
        if DEDOUBLONNER:
            s = signature(o)
            if s in vues:
                doublons += 1
                continue
            vues.add(s)
        gardees.append(o)
    stats = {
        "brutes": len(brutes),
        "gardees": len(gardees),
        "exclues": len(exclues),
        "doublons": doublons,
        "compte": compte,
    }
    return gardees, exclues, stats

def collecter(token, config):
    """Recherche France Travail puis filtrage. Conserve l'usage du CLI."""
    return filtrer(rechercher(token, config), config.get("jours_max"))

# === 6. SOURCE ADZUNA =======================================================
ADZUNA_SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{pays}/search/{page}"

def _adzuna_salaire(j):
    bas, haut = j.get("salary_min"), j.get("salary_max")
    if bas and haut and bas != haut:
        return f"{int(bas)} - {int(haut)} EUR/an"
    if bas:
        return f"{int(bas)} EUR/an"
    return ""

def _adzuna_vers_offre(j):
    """Convertit un resultat Adzuna au format interne (style France Travail)."""
    contrat = {"permanent": "CDI", "contract": "CDD"}.get(j.get("contract_type"), "")
    return {
        "intitule": j.get("title", ""),
        "entreprise": {"nom": (j.get("company") or {}).get("display_name", "")},
        "lieuTravail": {"libelle": (j.get("location") or {}).get("display_name", "")},
        "typeContratLibelle": contrat or (j.get("contract_time") or "").replace("_", " "),
        "typeContrat": contrat,
        "dateCreation": j.get("created", ""),
        "description": j.get("description", ""),
        "origineOffre": {"urlOrigine": j.get("redirect_url", "")},
        "salaire": {"libelle": _adzuna_salaire(j)},
        "_source": "Adzuna",
    }

TRANSITOIRES = (429, 500, 502, 503, 504)

def _get_avec_retries(url, params, timeout=20, tentatives=3):
    """GET avec reessais sur erreurs reseau ou statuts transitoires (5xx/429)."""
    derniere = None
    for n in range(tentatives):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code not in TRANSITOIRES:
                return r
            derniere = f"HTTP {r.status_code}"
        except requests.exceptions.RequestException as e:
            derniere = str(e)
        if n < tentatives - 1:
            time.sleep(1.5 * (n + 1))
    raise RuntimeError(f"service indisponible après {tentatives} essais ({derniere})")

def rechercher_adzuna(config, app_id, app_key):
    """Recherche Adzuna (pays 'fr' par defaut). Retourne des offres au format interne."""
    pays = config.get("pays_adzuna", "fr")
    base = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "what": mots_cles_recherche(config["mots_cles"]),
        "content-type": "application/json",
    }
    if config.get("where_adzuna"):
        base["where"] = config["where_adzuna"]
    if config.get("jours_max"):
        base["max_days_old"] = config["jours_max"]
    if config.get("type_contrat") == "CDI":
        base["permanent"] = 1
    elif config.get("type_contrat") == "CDD":
        base["contract"] = 1
    offres, page = [], 1
    while len(offres) < config["max_offres"]:
        url = ADZUNA_SEARCH_URL.format(pays=pays, page=page)
        r = _get_avec_retries(url, base)
        if r.status_code != 200:
            raise RuntimeError(f"Adzuna {r.status_code}")
        lot = r.json().get("results", [])
        if not lot:
            break
        offres.extend(_adzuna_vers_offre(j) for j in lot)
        if len(lot) < base["results_per_page"]:
            break
        page += 1
        time.sleep(0.3)
    return offres[: config["max_offres"]]

# === 7. SOURCE JOOBLE =======================================================
JOOBLE_URL = "https://jooble.org/api/{cle}"

def _jooble_vers_offre(j):
    """Convertit un resultat Jooble au format interne (style France Travail)."""
    return {
        "intitule": j.get("title", ""),
        "entreprise": {"nom": j.get("company", "")},
        "lieuTravail": {"libelle": j.get("location", "")},
        "typeContratLibelle": j.get("type", ""),
        "typeContrat": "",
        "dateCreation": j.get("updated", ""),
        "description": j.get("snippet", ""),
        "origineOffre": {"urlOrigine": j.get("link", "")},
        "salaire": {"libelle": j.get("salary", "")},
        "_source": "Jooble",
    }

def _post_jooble(url, corps, timeout=30, tentatives=3):
    """POST avec reessais sur erreurs reseau (Jooble est parfois lent)."""
    derniere = None
    for n in range(tentatives):
        try:
            return requests.post(url, json=corps, timeout=timeout)
        except requests.exceptions.RequestException as e:
            derniere = e
            if n < tentatives - 1:
                time.sleep(1.5 * (n + 1))
    raise RuntimeError(f"Jooble injoignable après {tentatives} essais : {derniere}")

def rechercher_jooble(config, api_key):
    """Recherche Jooble (POST JSON). Retourne des offres au format interne.

    Jooble ne filtre pas par type de contrat via l'API : le filtrage CDI/CDD
    se fait en aval (classer/filtrer) ou via les mots-cles.
    """
    offres, page, par_page = [], 1, 20
    while len(offres) < config["max_offres"]:
        corps = {
            "keywords": mots_cles_recherche(config["mots_cles"]),
            "page": page,
            "ResultOnPage": par_page,
        }
        if config.get("location_jooble"):
            corps["location"] = config["location_jooble"]
        r = _post_jooble(JOOBLE_URL.format(cle=api_key), corps)
        if r.status_code != 200:
            raise RuntimeError(f"Jooble {r.status_code} : {r.text[:200]}")
        lot = r.json().get("jobs", [])
        if not lot:
            break
        offres.extend(_jooble_vers_offre(j) for j in lot)
        if len(lot) < par_page:
            break
        page += 1
        time.sleep(0.3)
    return offres[: config["max_offres"]]

def main():
    load_dotenv(override=True)
    cid, secret = os.getenv("FT_CLIENT_ID"), os.getenv("FT_CLIENT_SECRET")
    if not cid or not secret:
        print("Identifiants manquants. Cree un fichier .env (voir .env.example).")
        sys.exit(1)
    print("Authentification...")
    try:
        token = obtenir_token(cid, secret)
    except (requests.HTTPError, RuntimeError) as e:
        print(f"Echec d'authentification : {e}")
        sys.exit(1)
    print(f"Recherche : {CONFIG['mots_cles']}")
    gardees, exclues, stats = collecter(token, CONFIG)
    print(f"   {stats['brutes']} offres recuperees.\n")
    compte, doublons = stats["compte"], stats["doublons"]
    for raison, n in sorted(compte.items(), key=lambda x: -x[1]):
        print(f"   x {n:>3} exclues - {raison}")
    if DEDOUBLONNER:
        print(f"   x {doublons:>3} doublons fusionnes")
    print(f"   = {len(gardees)} offres retenues\n")
    for o in gardees:
        ent = nom_entreprise(o) or "Entreprise non precisee"
        lieu = o.get("lieuTravail", {}).get("libelle", "-")
        contrat = o.get("typeContratLibelle", o.get("typeContrat", ""))
        url = o.get("origineOffre", {}).get("urlOrigine", "")
        print(f"- {o.get('intitule', '-')}  |  {ent}")
        print(f"  {lieu} | {contrat}")
        if url:
            print(f"  {url}")
        print()
    champs = ["intitule", "entreprise", "lieu", "contrat", "date", "url"]
    ecrire_csv([ligne(o) for o in gardees], CONFIG["fichier_csv"], champs)
    ecrire_csv(exclues, CONFIG["fichier_exclues"], champs + ["raison_exclusion"])
    print(f"{len(gardees)} offres dans {CONFIG['fichier_csv']}")
    print(f"{len(exclues)} offres ecartees dans {CONFIG['fichier_exclues']} (a verifier)")

if __name__ == "__main__":
    main()