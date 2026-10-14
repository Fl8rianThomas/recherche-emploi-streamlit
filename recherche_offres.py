#!/usr/bin/env python3
"""Recherche d'offres : cœur commun (normalisation, filtres ESN/titre/ancienneté,
dédoublonnage, scoring, lieux) + sources France Travail, Adzuna, Jooble, jobup.ch.

Les sites carrières des grands groupes (Workday, LVMH, BPCE…) sont dans
sites_carrieres.py, qui réutilise les utilitaires de ce module.
"""

import os
import re
import csv
import math
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
    # Hors CDI vise : stages, alternances (deja ecartes cote sites carrieres)
    "stage", "stagiaire", "alternance", "alternant", "apprenti",
    # Data Analyst : metier voisin, non vise (demande de l'utilisateur) ; on garde
    # les postes mixtes (« Business Data Analyst », « Data Analyst / Business Analyst »).
    "data analyst sauf business, fonctionnel, product owner",
    "data analyste sauf business, fonctionnel, product owner",
    "analyste data sauf business, fonctionnel, product owner",
    "analyste de donnees sauf business, fonctionnel, product owner",
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

# Domicile de l'utilisateur : centre de l'onglet 🗺️ (carte) et des recherches
# Adzuna « autour de chez moi » (les recherches nationales plafonnent a 200
# resultats, dominees par Paris : les offres de la region y sont noyees).
DOMICILE = "Lamotte-Beuvron"
RAYON_DOMICILE_KM = 100

# Grands groupes (onglet 🏢) : une offre France dont l'ENTREPRISE contient un de
# ces noms (mot entier, accents/casse ignores) y est rangee. Editable dans l'interface.
# Secteurs retenus : banque/finance, assurance, luxe/mode, aero/defense,
# energie/industrie/BTP, sante (conso/distribution et telecom/transport/services
# ecartes a la demande de l'utilisateur).
GRANDS_GROUPES_PAR_DEFAUT = [
    # Banque / finance
    "BNP Paribas", "Société Générale", "Crédit Agricole", "LCL", "Natixis", "BPCE",
    "Caisse d'Épargne", "Banque Populaire", "Crédit Mutuel", "CIC",
    "La Banque Postale", "HSBC", "Amundi", "CACEIS", "Citi", "Rothschild", "Ardian",
    "Ayvens", "Coface",
    # Assurance / mutuelles
    "AXA", "Allianz", "Generali", "CNP Assurances", "Groupama", "Malakoff Humanis",
    "AG2R", "MAIF", "MACIF", "Covéa", "Marsh McLennan", "Matmut", "Harmonie Mutuelle",
    "VYV", "MGEN", "Swiss Life", "April", "Aésio", "Klesia", "Apicil", "Pro BTP", "MAAF",
    "MMA", "GMF", "Thélem", "Mutuelle Générale", "SMABTP",
    # Luxe / beauté / mode
    "LVMH", "Louis Vuitton", "Dior", "Sephora", "Moët Hennessy", "Givenchy",
    "Kering", "Saint Laurent", "Chanel", "Hermès", "L'Oréal", "Loreal", "Cartier",
    "Richemont", "PVH",
    # Aéronautique / défense
    "Airbus", "Safran", "Thales", "Dassault", "MBDA", "Naval Group", "ArianeGroup",
    # Énergie / industrie / BTP
    "TotalEnergies", "EDF", "Engie", "Schneider Electric", "Air Liquide",
    "Saint-Gobain", "Michelin", "Renault", "Stellantis", "Alstom", "Veolia",
    "^Vinci", "Bouygues", "Valeo", "Eiffage", "Imerys", "Bosch",
    "SNCF",                 # gardee a la demande (secteur transport ecarte sinon)
    # Santé (labos, medtech, CRO, e-santé — hors hôpitaux et cliniques)
    "Sanofi", "Servier", "Pierre Fabre", "LFB", "Ipsen", "Eurofins", "Novo Nordisk",
    "Shiseido", "Guerbet", "Coty", "Boehringer Ingelheim", "Cegedim", "Expanscience",
    "Henry Schein", "Essity", "Danaher", "Stryker", "Smith+Nephew", "Pfizer", "Roche",
    "GSK", "Johnson & Johnson", "Becton Dickinson", "Thermo Fisher", "IQVIA", "Abbott",
    "GE HealthCare", "Biocodex", "Opella", "Sartorius", "Syneos Health", "Medline",
    "Lonza", "Kenvue", "Fresenius", "Viatris", "Baxter", "Novartis", "Sandoz", "Alcon",
    "Edwards Lifesciences", "Catalent", "Medtronic", "Revvity", "Amgen", "Zentiva",
    "Biogen", "Eli Lilly", "Takeda", "MSD", "AstraZeneca", "Philips", "AbbVie",
    "Biogaran", "Siemens Healthineers", "Merck", "Doctolib", "^Alan SA", "Lifen",
    "Owkin", "Nabla", "Inato",
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
    # Generique : tout nom contenant « recrutement » / « recruitment » (AK Recrutement,
    # LHH Recruitment Solutions, LEA Recrutement…) = cabinet de recrutement.
    "recrutement", "recruitment",
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
# Employeurs directs HORS secteurs de l'onglet 🏢, exclus a tort par le test texte
# (« nos clients » = leurs propres clients) : audit du 2026-10-14.
EMPLOYEURS_DIRECTS = {"air france", "airfrance", "isagri"}
REGEX_EMPLOYEURS_DIRECTS = construire_regex(EMPLOYEURS_DIRECTS)

def nom_entreprise(o):
    return o.get("entreprise", {}).get("nom", "").strip()

def regex_groupes(groupes):
    """Compile la liste des grands groupes -> (libelles, regex).

    « Nom » = mot entier n'importe ou dans le nom d'entreprise ; « ^Nom » = le nom
    d'entreprise COMMENCE par Nom (evite « Sup de Vinci », « Nihon Kohden Orange »),
    ou Nom suit une parenthese (« Sofrecom (Orange) », forme des sites carrieres).
    """
    entrees = [g.strip() for g in groupes if g and g.strip().lstrip("^").strip()]
    entrees.sort(key=lambda g: -len(g))              # le plus specifique d'abord
    libelles, motifs = {}, []
    for i, g in enumerate(entrees):
        debut = g.startswith("^")
        nom = g.lstrip("^").strip()
        libelles[f"g{i}"] = nom
        prefixe = r"(?:^|\()" if debut else r"\b"
        motifs.append(f"(?P<g{i}>{prefixe}{re.escape(normaliser(nom))}\\b)")
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
        if (_REGEX_GROUPES and _REGEX_GROUPES.search(ent)) or REGEX_EMPLOYEURS_DIRECTS.search(ent):
            # Grand groupe / employeur direct : « nos clients », « cabinet de conseil »
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
    code = departement_offre(o)                            # commune, code postal, ville
    return DEPT_REGION.get(code, "Autre") if code else "Autre"

# Noms de chefs-lieux -> on les detecte aussi au milieu d'un libelle (« Paris 8e »,
# « Tours Centre »). Tries du plus long au plus court pour matcher en priorite les
# noms composes (« Le Mans » avant « Mans »).
_REGEX_VILLES_DEPT = re.compile(
    "|".join(r"\b" + re.escape(v) + r"\b"
             for v in sorted(_NOM_VERS_CODE, key=len, reverse=True))
)

# Communes de France (>= 1000 hab.) + noms de departements -> code departement,
# pour les villes qui ne sont pas des chefs-lieux (Joue-les-Tours, Olivet, Puteaux…)
# et les libelles « Haute-Garonne, Occitanie ». Fichier genere depuis geo.api.gouv.fr
# (voir son en-tete) ; homonymes -> la commune la plus peuplee.
_FICHIER_COMMUNES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "communes_departements.tsv")

def _cle_commune(nom):
    """« St-Jean-de-Braye » -> « saint jean de braye » (cle du fichier des communes)."""
    n = normaliser(nom).replace("-", " ").replace("'", " ").strip()
    n = re.sub(r"^st(e?)\s", lambda m: "saint" + m.group(1) + " ", n)
    return re.sub(r"\s+", " ", n)

def _charger_communes():
    """-> ({cle: departement}, {cle: (latitude, longitude)}) depuis le fichier TSV
    (cle \\t departement \\t latitude \\t longitude ; coordonnees vides pour les
    noms de departement). Cles = communes normalisees ET codes postaux."""
    deps, coords = {}, {}
    try:
        with open(_FICHIER_COMMUNES, encoding="utf-8") as f:
            for l in f:
                if not l.strip() or l.startswith("#"):
                    continue
                champs = l.rstrip("\r\n").split("\t")          # CRLF sous Windows
                k, dep = champs[0], champs[1].strip() if len(champs) > 1 else ""
                deps[k] = dep
                if len(champs) >= 4 and champs[2].strip() and champs[3].strip():
                    coords[k] = (float(champs[2]), float(champs[3]))
    except (OSError, ValueError):
        pass
    return deps, coords

_COMMUNES, _COORDS = _charger_communes()

def departement_offre(o):
    """Devine le code departement (« 37 », « 75 »…) d'une offre, ou None.

    Gere : prefixe numerique (« 37 - Tours », FT), code postal complet
    (« 75008 », « 75008 Paris »), nom de commune (« Puteaux, Ile-de-France »,
    « Joue-les-Tours ») et nom de ville chef-lieu present n'importe ou dans le
    libelle (« Paris 8e », « Paris (75) », « Toulouse Area »).
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
    for part in re.split(r"[,(/;]| - ", lieu):            # « Puteaux, Ile-de-France »
        code = _COMMUNES.get(_cle_commune(part))
        if code:
            return code
    mv = _REGEX_VILLES_DEPT.search(norm)                  # « Paris 8e » -> Paris
    if mv:
        return _NOM_VERS_CODE.get(mv.group(0))
    return None

def coords_lieu(texte):
    """(latitude, longitude) d'un lieu libre (« Lamotte-Beuvron », « 41600 »,
    « 41 - Blois », « Paris 8e »…), ou None. Code postal, puis commune, puis
    chef-lieu cite dans le texte."""
    if not (texte or "").strip():
        return None
    for cp in re.findall(r"\b(\d{5})\b", texte):
        if cp in _COORDS:
            return _COORDS[cp]
    for part in re.split(r"[,(/;]| - ", texte):
        k = _cle_commune(part)
        if k in _COORDS:
            return _COORDS[k]
    mv = _REGEX_VILLES_DEPT.search(normaliser(texte))
    return _COORDS.get(_cle_commune(mv.group(0))) if mv else None

# Localites suisses (GeoNames, noms FR/DE/IT/EN : Geneve / Genf / Geneva) pour
# placer les offres de l'onglet Suisse sur la carte (voir l'en-tete du fichier).
_FICHIER_SUISSE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "communes_suisse.tsv")

def _charger_suisse():
    coords = {}
    try:
        with open(_FICHIER_SUISSE, encoding="utf-8") as f:
            for l in f:
                if l.strip() and not l.startswith("#"):
                    k, la, lo = l.rstrip("\r\n").split("\t")[:3]
                    coords[k] = (float(la), float(lo))
    except (OSError, ValueError):
        pass
    return coords

_COORDS_CH = _charger_suisse()

def coords_suisse(texte):
    """(latitude, longitude) d'un lieu suisse libre (« Geneva, Lausanne »,
    « EMEA > CHE > Stabio », « Grand-Lancy »), ou None : 1re localite reconnue ;
    a defaut, le dernier mot d'un nom compose (« Grand-Lancy » -> Lancy)."""
    for part in re.split(r"[,/;:>&|()]", html.unescape(texte or "")):
        k = _cle_commune(part)
        if k in _COORDS_CH:
            return _COORDS_CH[k]
        mots = k.split()
        for i in range(1, len(mots)):
            for essai in (" ".join(mots[i:]),            # « grand lancy » -> « lancy »
                          " ".join(mots[:-i])):          # « lausanne vd » -> « lausanne »
                if essai in _COORDS_CH:
                    return _COORDS_CH[essai]
    return None

def geoloc_offre(o):
    """(latitude, longitude) du lieu de l'offre, ou None : coordonnees fournies par
    la source (France Travail, Adzuna, Jibe, Phenom), sinon libelle -> localites
    suisses pour une offre en Suisse, code postal / commune / chef-lieu sinon."""
    lt = o.get("lieuTravail") or {}
    try:
        if lt.get("latitude") not in (None, "") and lt.get("longitude") not in (None, ""):
            return float(lt["latitude"]), float(lt["longitude"])
    except (TypeError, ValueError):
        pass
    if o.get("_source") == "jobup.ch" or est_suisse(o):
        return coords_suisse(lt.get("libelle", ""))
    return coords_lieu(f'{lt.get("codePostal") or ""} {lt.get("libelle", "") or ""}')

def distance_km(a, b):
    """Distance a vol d'oiseau (km) entre deux points (latitude, longitude)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))

SEUIL_SCORE_ELEVE = 8    # « score élevé » de la carte (≈ 20 % des offres, oct. 2026)

def rang_carte(rh, score, localisation, esn, grand_groupe, seuil=SEUIL_SCORE_ELEVE):
    """Niveau d'une offre sur la carte (1 = meilleur), hiérarchie de l'utilisateur :
    1 RH + score élevé + localisation + sans ESN + grand groupe
    2 RH + score élevé + sans ESN + grand groupe
    3 RH + score élevé + sans ESN
    4 RH + score élevé + localisation
    5 RH + score élevé
    6 score élevé
    7 autre
    (localisation = département favori 🎯 ; RH = domaine RH/SIRH détecté 👥)."""
    eleve = score >= seuil
    if rh and eleve:
        if not esn and grand_groupe:
            return 1 if localisation else 2
        if not esn:
            return 3
        return 4 if localisation else 5
    return 6 if eleve else 7

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

def regles_exclusion(termes):
    """Lignes d'exclusion -> [(mot, [exceptions])] normalises.

    « mot » exclut tout titre contenant mot ; « mot sauf a, b » l'exclut SAUF si le
    titre contient aussi a ou b (ex. « data analyst sauf business » ecarte
    « Data Analyst » mais garde « Business Data Analyst », poste mixte BA).
    """
    regles = []
    for t in termes or []:
        mot, _, sauf = normaliser(t).partition(" sauf ")
        if mot.strip():
            regles.append((mot.strip(), [s.strip() for s in sauf.split(",") if s.strip()]))
    return regles

def terme_exclu(titre_norm, regles):
    """Premier mot d'exclusion present dans le titre (hors exceptions), ou None."""
    for mot, sauf in regles:
        if mot in titre_norm and not any(s in titre_norm for s in sauf):
            return mot
    return None

def signatures_esn(brutes):
    """{signature: raison} des offres dont AU MOINS UNE copie est classee ESN.

    Une meme offre arrive souvent par plusieurs sources avec une description plus ou
    moins complete (France Travail : texte entier ; Adzuna/Jooble : extrait) : la
    copie complete peut trahir une ESN (« nos clients », « en prestation ») que
    l'extrait cache. Toutes les copies doivent alors etre traitees comme ESN
    (cas reel : « Product Owner Technique », Centifolia Conseil, oct. 2026).
    """
    sig = {}
    for o in brutes:
        ok, raison = classer(o)
        if not ok:
            sig.setdefault(signature(o), raison)
    return sig

def est_esn(o, sig_esn):
    """True si l'offre (ou une autre copie de la meme offre) est une ESN."""
    return signature(o) in sig_esn

def filtrer(brutes, jours_max=None, exclure_titres=None, inclure_titres=None,
            ignorer_esn=False):
    """Filtrage ESN + titre + ancienneté + dedoublonnage sur une liste d'offres.

    jours_max     : exclut les offres parues il y a plus de N jours (None = aucune
                    limite). Les offres sans date exploitable sont conservees.
    inclure_titres: si non vide, ne garde que les offres dont le titre contient
                    au moins un de ces mots (filtre de pertinence).
    exclure_titres: liste de mots qui, presents dans le titre, excluent l'offre
                    (syntaxe « mot sauf a, b » : voir regles_exclusion).
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
    exclus = regles_exclusion(exclure_titres)
    inclus = [normaliser(t) for t in (inclure_titres or []) if t.strip()]
    gardees, exclues, vues, compte, doublons = [], [], set(), {}, 0
    sig_esn = {} if ignorer_esn else signatures_esn(brutes)
    for o in brutes:
        # Classement au niveau de l'OFFRE (toutes ses copies) : voir signatures_esn.
        raison = sig_esn.get(signature(o))
        garder, raison = (False, raison) if raison else (True, "")
        titre = normaliser(o.get("intitule", ""))
        if garder and inclus and not any(t in titre for t in inclus):
            garder, raison = False, "hors périmètre (titre)"
        if garder and exclus:
            term = terme_exclu(titre, exclus)
            if term:
                garder, raison = False, f"titre exclu ({term})"
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
        "lieuTravail": {"libelle": (j.get("location") or {}).get("display_name", ""),
                        "latitude": j.get("latitude"), "longitude": j.get("longitude")},
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
        if config.get("distance_adzuna"):          # rayon (km) autour de « where »
            base["distance"] = config["distance_adzuna"]
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