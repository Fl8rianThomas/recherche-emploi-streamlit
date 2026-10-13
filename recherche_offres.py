#!/usr/bin/env python3
"""Recherche d'offres France Travail avec exclusion des ESN + dedoublonnage."""

import os
import re
import csv
import sys
import time
import html
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
    "product owner",
    "product manager",
]

# Le TITRE doit contenir au moins un de ces mots (sinon l'offre est ecartee).
# Vise le coeur du metier : evite le bruit capte via la description seule.
# Syntaxe « mot:couleur » -> colore la ligne entiere dans la liste (par metier).
# Couleurs : violet, bleu, vert, orange, rouge, jaune, rose, cyan, gris.
# L'ordre compte pour la couleur : la 1re couleur trouvee dans le titre gagne
# (PO en tete -> une offre « Business Analyst / Product Owner » est violette).
# NB : "owner"/"produit" ciblent PO & Chef de produit sans attraper "production".
INCLUSIONS_TITRE_PAR_DEFAUT = [
    "owner:orange",            # product owner, proxy product owner
    "product manager:orange",  # PO digital (on EVITE "produit" -> chef de produit
                               # marketing / chef de rayon = hors sujet)
    "analyst:bleu",            # couvre analyst + analyste
    "business:bleu",
    "amoa:bleu",
    "fonctionnel:bleu",
]

# Mots dont la presence dans le TITRE exclut l'offre (metiers voisins / hors sujet).
# Editable dans l'interface.
EXCLUSIONS_TITRE_PAR_DEFAUT = [
    "consultant",
    "eaux usees",
    "assainissement",
    "reseaux humides",
    "affaires reglementaires",
    "chef de rayon",        # responsable de rayon (supermarche), hors sujet
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

# Departements favoris (Optimum) : une offre qui s'y trouve recoit l'icone 🎯.
# 37 Indre-et-Loire (Tours), 41 Loir-et-Cher (Blois), 45 Loiret (Orleans),
# 74 Haute-Savoie (Annecy), 75 Paris.
DEPTS_FAVORIS_PAR_DEFAUT = ["37", "41", "45", "74", "75"]

# Grands groupes (onglet 🏢) : une offre France dont l'ENTREPRISE contient un de
# ces noms (mot entier, accents/casse ignores) y est rangee. Editable dans l'interface.
GRANDS_GROUPES_PAR_DEFAUT = [
    # Banques
    "BNP Paribas", "Société Générale", "Crédit Agricole", "LCL", "Natixis", "BPCE",
    "Caisse d'Épargne", "Banque Populaire", "Crédit Mutuel", "CIC",
    "La Banque Postale", "HSBC", "Amundi", "CACEIS",
    # Assurance / mutuelles
    "AXA", "Allianz", "Generali", "CNP Assurances", "Groupama", "Malakoff Humanis",
    "AG2R", "MAIF", "MACIF", "Covéa",
    # Luxe / beauté
    "LVMH", "Louis Vuitton", "Dior", "Sephora", "Moët Hennessy", "Givenchy",
    "Kering", "Saint Laurent", "Chanel", "Hermès", "L'Oréal", "Loreal", "Cartier",
    # Aéronautique / défense
    "Airbus", "Safran", "Thales", "Dassault", "MBDA", "Naval Group", "ArianeGroup",
    # Énergie / industrie / services
    "TotalEnergies", "EDF", "Engie", "Schneider Electric", "Air Liquide",
    "Saint-Gobain", "Michelin", "Renault", "Stellantis", "Alstom", "Veolia",
    "^Vinci", "Bouygues", "^Orange", "SNCF", "RATP", "La Poste",
    # Santé / grande conso / distribution
    "Sanofi", "Servier", "Danone", "Pernod Ricard", "Carrefour", "Decathlon", "Accor",
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

# Termes RH/SIRH (FR + EN) pour reperer une offre du domaine RH DIRECTEMENT dans
# son texte (titre + description), independamment du profil Optimum editable.
# Sert au badge 👥 de l'onglet International (offres surtout anglophones).
MOTS_RH = {
    "ressources humaines", "rh", "sirh", "human resources", "hris", "hcm",
    "hr systems", "hr data", "hr operations", "hr information", "people operations",
    "people analytics", "gpec", "administration du personnel", "gestion du personnel",
    "paie", "payroll", "remuneration", "compensation", "benefits", "rewards",
    "talent", "talent management", "talent acquisition", "recrutement",
    "recruitment", "recruiting", "formation", "learning and development",
    "workday", "successfactors", "sap sf", "talentsoft", "gta",
    "employee experience", "workforce",
}

def offre_rh(o):
    """True si l'offre releve du domaine RH (scan direct de l'intitule + description)."""
    return bool(_REGEX_RH.search(normaliser(
        o.get("intitule", "") + " " + (o.get("description") or ""))))

def mots_rh_offre(o):
    """Termes RH detectes dans l'offre (pour affichage des chips)."""
    txt = normaliser(o.get("intitule", "") + " " + (o.get("description") or ""))
    return sorted({m for m in MOTS_RH
                   if re.search(r"\b" + re.escape(normaliser(m)) + r"\b", txt)})

# Detecteur Product Owner DIGITAL (2e profil cible) -> couleur de ligne.
# On evite "chef de produit" (marketing) et "produit" (chef de rayon).
RE_PRODUCT_OWNER = re.compile(
    r"product owner|product manager|proxy[ -]?(product )?owner|\bowner\b")

def est_product_owner(o):
    """True si l'intitule designe un poste Product Owner / Product Manager."""
    return bool(RE_PRODUCT_OWNER.search(normaliser(o.get("intitule", ""))))

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

# Employeurs "relais" (forums, agregateurs) qui postent pour le compte d'autres
# entreprises : on les traite comme anonymes pour detecter une ESN citee dans la
# description (ex: « engagements Capgemini » via Forums Talents Handicap).
AGREGATEURS = {
    "forums talents handicap", "talents handicap", "hellowork", "hello work",
    "meteojob", "jobijoba",
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
_REGEX_RH = construire_regex(MOTS_RH)
REGEX_AGREGATEURS = construire_regex(AGREGATEURS)

def nom_entreprise(o):
    return o.get("entreprise", {}).get("nom", "").strip()

def regex_groupes(groupes):
    """Compile la liste des grands groupes -> (libelles, regex).

    « Nom » = mot entier n'importe ou dans le nom d'entreprise ; « ^Nom » = le nom
    d'entreprise COMMENCE par Nom (evite « Sup de Vinci », « Nihon Kohden Orange »).
    """
    entrees = [g.strip() for g in groupes if g and g.strip().lstrip("^").strip()]
    entrees.sort(key=lambda g: -len(g))              # le plus specifique d'abord
    libelles, motifs = {}, []
    for i, g in enumerate(entrees):
        debut = g.startswith("^")
        nom = g.lstrip("^").strip()
        libelles[f"g{i}"] = nom
        motifs.append(f"(?P<g{i}>{'^' if debut else r'\b'}{re.escape(normaliser(nom))}\\b)")
    return libelles, re.compile("|".join(motifs)) if motifs else None

def groupe_offre(o, libelles, rx):
    """Libelle du grand groupe qui publie l'offre (via regex_groupes), ou ""."""
    if rx is None:
        return ""
    m = rx.search(normaliser(nom_entreprise(o)))
    return libelles.get(m.lastgroup, "") if m else ""

_LIB_GROUPES, _REGEX_GROUPES = regex_groupes(GRANDS_GROUPES_PAR_DEFAUT)

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
    # Employeur "relais" (forum/agregateur) : on regarde l'ESN dans le texte.
    relais = bool(ent) and bool(REGEX_AGREGATEURS.search(ent))
    if not ent or relais:
        # Entreprise masquee/relais : un nom d'ESN connue dans le texte trahit
        # l'employeur (ex: offre anonyme ou forum dont la description dit
        # "engagements Capgemini...").
        if EXCLURE_PAR_TEXTE and REGEX_ESN.search(txt):
            return False, "ESN (texte)"
        if not ent and EXCLURE_ANONYMES:
            return False, "entreprise anonyme"
    else:
        if REGEX_ESN.search(ent):
            return False, "ESN (nom)"
        if _REGEX_GROUPES and _REGEX_GROUPES.search(ent):
            # Grand groupe = employeur direct : « nos clients », « cabinet de conseil »
            # dans sa description ne trahissent pas une ESN -> pas de test texte.
            return True, ""
    if EXCLURE_PAR_TEXTE and REGEX_SIGNAUX.search(txt):
        return False, "indice ESN dans le texte"
    return True, ""

def signature(o):
    titre = normaliser(o.get("intitule", ""))
    titre = re.sub(r"[\(\[]?\s*[hfmnd](\s*/\s*[hfmnd])+\s*[\)\]]?", " ", titre)
    titre = re.sub(r"[^a-z0-9]+", " ", titre)   # ponctuation/% -> dedup + robuste
    titre = re.sub(r"\s+", " ", titre).strip()
    return titre + "||" + normaliser(nom_entreprise(o))

# Detecteur d'offre redigee en ALLEMAND (pour ne garder, en Suisse, que les
# offres francophones + anglophones). Mots-outils + vocabulaire d'annonce DE +
# morphologie (mots en -ung/-schaft/-keit...) + forme inclusive « .../in ».
MARQUEURS_ALLEMAND = {
    "und", "der", "die", "das", "fur", "mit", "sie", "wir", "ein", "eine", "im",
    "zu", "von", "auf", "ist", "werden", "wird", "bei", "aus", "dem", "den",
    "oder", "auch", "als", "sich", "nicht", "wie", "unsere", "unser", "sind",
    "haben", "dieser", "diese", "du", "dich", "dir", "deine", "deinen", "deiner",
    "uns", "einer", "einen", "nach", "uber", "unter", "durch", "zum", "zur",
    "gerne", "sowie", "ihre", "ihren", "ihr",
    # vocabulaire d'annonce RH allemand
    "aufgaben", "kenntnisse", "erfahrung", "bereich", "stelle", "mitarbeiter",
    "berufserfahrene", "unbefristet", "pensum", "arbeitsort", "stellenantritt",
    "bewerbung", "bewerben", "jetzt", "schweiz", "zukunft", "gestalten", "umfeld",
    "begeisterst", "erwartet", "anforderungen", "verantwortung", "spannende",
    "standort", "profil", "rolle", "wochen", "taglich",
}

def est_allemand(o):
    """True si l'offre est redigee en allemand (titre, description ou entreprise)."""
    titre = o.get("intitule", "")
    if re.search(r"/-?in\b", titre, re.I) or "analystin" in normaliser(titre):
        return True
    mots = re.findall(r"[a-z]+", normaliser(
        titre + " " + (o.get("description") or "") + " " + nom_entreprise(o)))
    fonct = len(MARQUEURS_ALLEMAND & set(mots))
    morpho = sum(1 for m in mots
                 if len(m) > 6 and m.endswith(("ung", "ungen", "schaft", "keit", "heit")))
    return fonct >= 2 or morpho >= 2

# === 4. API FRANCE TRAVAIL ==================================================
TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

def obtenir_token(cid, secret):
    data = {"grant_type": "client_credentials", "client_id": cid,
            "client_secret": secret, "scope": SCOPE}
    derniere = None
    for n in range(4):
        try:
            r = requests.post(TOKEN_URL, data=data, timeout=20)
            if r.status_code == 200:
                return r.json()["access_token"]
            if r.status_code not in TRANSITOIRES:
                raise RuntimeError(f"Code {r.status_code} - {r.text}")
            derniere = f"HTTP {r.status_code}"
        except requests.exceptions.RequestException as e:
            derniere = str(e)
        if n < 3:
            time.sleep(min(8, 2 ** n))
    raise RuntimeError(f"Authentification France Travail indisponible ({derniere})")

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
        try:
            r = _get_avec_retries(SEARCH_URL, params, headers=headers)
        except RuntimeError:
            if offres:          # page suivante KO : on garde ce qu'on a deja
                break
            raise               # premiere page KO : on remonte l'erreur
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

# Noms de chefs-lieux -> on les detecte aussi au milieu d'un libelle (« Paris 8e »,
# « Tours Centre »). Tries du plus long au plus court pour matcher en priorite les
# noms composes (« Le Mans » avant « Mans »).
_REGEX_VILLES_DEPT = re.compile(
    "|".join(r"\b" + re.escape(v) + r"\b"
             for v in sorted(_NOM_VERS_CODE, key=len, reverse=True))
)

def departement_offre(o):
    """Devine le code departement (« 37 », « 75 »…) d'une offre, ou None.

    Gere : prefixe numerique (« 37 - Tours », FT), code postal complet
    (« 75008 », « 75008 Paris »), et nom de ville chef-lieu present n'importe ou
    dans le libelle (« Paris 8e », « Paris (75) », « Paris, Ile-de-France »).
    """
    lieu = (o.get("lieuTravail") or {}).get("libelle", "") or ""
    if not lieu.strip():
        return None
    m = re.match(r"\s*(2[AB]|\d{2,3})\b", lieu)           # « 37 - Tours »
    if m:
        return m.group(1).upper()
    norm = normaliser(lieu)
    mcp = re.search(r"\b(\d{5})\b", norm)                 # code postal « 75008 »
    if mcp:
        cp = mcp.group(1)
        return cp[:3] if cp.startswith("97") else cp[:2]  # DOM 97x sinon 2 chiffres
    mv = _REGEX_VILLES_DEPT.search(norm)                  # « Paris 8e » -> Paris
    if mv:
        return _NOM_VERS_CODE.get(mv.group(0))
    return None

def est_departement_favori(o, codes):
    """True si l'offre est localisee dans l'un des departements `codes`."""
    if not codes:
        return False
    dep = departement_offre(o)
    return bool(dep) and dep in codes

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

# Villes / cantons suisses (normalises) pour l'onglet Suisse. On detecte aussi
# une ville suisse dans l'intitule (ex: « Analyste - Geneve »), en excluant le
# faux « Geneva, IL » (Illinois, USA).
SUISSE_NOMS = {
    "suisse", "switzerland", "swiss", "schweiz", "svizzera",
    "geneve", "geneva", "genf", "lausanne", "zurich", "zuerich", "bale", "basel",
    "berne", "bern", "lugano", "fribourg", "freiburg", "neuchatel", "sion",
    "sierre", "lucerne", "luzern", "winterthur", "st gallen", "saint-gall",
    "vaud", "valais", "nyon", "montreux", "vevey", "morges", "carouge",
    "yverdon", "bienne", "biel", "martigny", "renens", "meyrin", "gland",
    "la chaux-de-fonds", "plan-les-ouates", "rolle", "crissier",
}
REGEX_SUISSE = construire_regex(SUISSE_NOMS)

def est_suisse(o):
    """True si l'offre est localisee en Suisse (lieu ou ville suisse dans le titre).

    Recherche par frontiere de mot (robuste au « Geneve h/f » colle), en excluant
    le faux « Geneva, IL » (Illinois, USA).
    """
    lieu = (o.get("lieuTravail") or {}).get("libelle", "") or ""
    m = re.search(r",\s*([A-Z]{2})\b", lieu)
    if m and m.group(1) in ETATS_US:        # « Geneva, IL » -> USA, pas la Suisse
        return False
    return bool(REGEX_SUISSE.search(normaliser(lieu + " " + o.get("intitule", ""))))

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

def _get_avec_retries(url, params, timeout=20, tentatives=4, headers=None):
    """GET avec reessais sur erreurs reseau ou statuts transitoires (5xx/429).

    Backoff exponentiel (1s, 2s, 4s...) pour laisser le temps a un service
    surcharge (503) ou a une connexion coupee (RST) de se retablir.
    """
    derniere = None
    for n in range(tentatives):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=headers)
            if r.status_code not in TRANSITOIRES:
                return r
            derniere = f"HTTP {r.status_code}"
        except requests.exceptions.RequestException as e:
            derniere = str(e)
        if n < tentatives - 1:
            time.sleep(min(8, 2 ** n))
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

# === 8. SOURCE JOBUP.CH (Suisse romande, francophone) =======================
# API JSON publique (non documentee) du job board suisse francophone jobup.ch.
# Toutes les offres sont en Suisse -> alimente directement l'onglet 🇨🇭.
JOBUP_URL = "https://www.jobup.ch/api/v1/public/search"

def _jobup_vers_offre(d):
    """Convertit un document jobup.ch au format interne."""
    liens = d.get("_links") or {}
    url = ((liens.get("detail_fr") or liens.get("detail_en") or {}).get("href")) or ""
    return {
        "intitule": d.get("title", "") or "",
        "entreprise": {"nom": d.get("company_name", "") or ""},
        "lieuTravail": {"libelle": d.get("place", "") or ""},
        "typeContratLibelle": "",
        "typeContrat": "",
        "dateCreation": d.get("publication_date", "") or "",
        "description": html.unescape(d.get("preview", "") or ""),
        "origineOffre": {"urlOrigine": url},
        "salaire": {"libelle": ""},
        "_source": "jobup.ch",
    }

def rechercher_jobup(config):
    """Recherche jobup.ch (Suisse romande, GET JSON public). Format interne."""
    # En-têtes « navigateur » plus complets : jobup.ch (Cloudflare) renvoie 403
    # depuis les IP de datacenter (GitHub Actions, Streamlit Cloud). Ces en-têtes
    # aident parfois ; si l'IP est bloquée, le 403 persiste (onglet 🇨🇭 limité).
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.8",
        "Referer": "https://www.jobup.ch/fr/emplois/",
        "Origin": "https://www.jobup.ch",
    }
    offres, page, rows = [], 1, 20      # l'API plafonne rows a 20
    while len(offres) < config["max_offres"]:
        params = {"query": mots_cles_recherche(config["mots_cles"]),
                  "rows": rows, "page": page}
        r = _get_avec_retries(JOBUP_URL, params, timeout=20, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"jobup.ch {r.status_code}")
        data = r.json()
        docs = data.get("documents") or []
        if not docs:
            break
        offres.extend(_jobup_vers_offre(x) for x in docs)
        if page >= (data.get("num_pages") or 1):
            break
        page += 1
        time.sleep(0.3)
    return offres[: config["max_offres"]]

# === 9. SOURCE WORKDAY (sites carrieres des grands groupes) =================
# Beaucoup de grands groupes publient leurs offres sur Workday, qui expose une API
# JSON publique (celle du site carriere) : recherche par mots-cles + filtre pays.
# Ajouter un groupe = ajouter (nom, URL publique de son site carriere Workday).
WORKDAY_GROUPES = [
    ("Airbus", "https://ag.wd3.myworkdayjobs.com/Airbus"),
    ("Thales", "https://thales.wd3.myworkdayjobs.com/Careers"),
    ("Sanofi", "https://sanofi.wd3.myworkdayjobs.com/SanofiCareers"),
    ("Chanel", "https://cc.wd3.myworkdayjobs.com/ChanelCareers"),
]
WORKDAY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"   # id pays « France » (commun aux tenants)
# Stages / alternances / VIE : ecartes (on vise des CDI, comme les autres sources).
_RE_WORKDAY_HORS_CDI = re.compile(
    r"\b(stage|stagiaire|intern|internship|alternance|alternant|apprenti|apprentie"
    r"|apprentissage|work[- ]study|v\.?i\.?e)\b")

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

def rechercher_workday(config, nom, url_site):
    """Offres France d'un site carriere Workday (format interne, entreprise = nom).

    config["inclure_titres"] / ["exclure_titres"] (optionnels) : pre-filtre sur le
    titre AVANT d'ouvrir la fiche detaillee (1 requete par offre) -> ~5x plus rapide.
    Les memes filtres sont de toute facon re-appliques ensuite par filtrer().
    """
    api = _workday_api(url_site)
    jours_max = config.get("jours_max")
    inclus = [normaliser(t) for t in config.get("inclure_titres") or [] if t.strip()]
    exclus = [normaliser(t) for t in config.get("exclure_titres") or [] if t.strip()]
    offres, offset, par_page = [], 0, 20          # l'API plafonne limit a 20
    total = None                                  # renvoye seulement en page 1
    pages_vides = 0                               # pages consecutives sans titre pertinent
    while len(offres) < config["max_offres"]:
        corps = {"appliedFacets": {"locationCountry": [WORKDAY_FRANCE]},
                 "limit": par_page, "offset": offset,
                 "searchText": mots_cles_recherche(config["mots_cles"])}
        data = _post_workday(api + "/jobs", corps, nom)
        if total is None:
            total = data.get("total") or 0
        lot = data.get("jobPostings") or []
        pertinents = 0
        for j in lot:
            titre = j.get("title", "") or ""
            tn = normaliser(titre)
            age = _workday_age_jours(j.get("postedOn"))
            if inclus and not any(t in tn for t in inclus):
                continue
            pertinents += 1
            if _RE_WORKDAY_HORS_CDI.search(tn):
                continue
            if any(t in tn for t in exclus):
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