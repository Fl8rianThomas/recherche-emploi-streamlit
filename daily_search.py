#!/usr/bin/env python3
"""Recherche quotidienne + envoi d'un e-mail récapitulatif (onglet France/Optimum).

Reproduit le pipeline de l'app (app.py) côté France : multi-variantes ×
multi-sources (France Travail, Adzuna, Jooble), filtrage ESN/titre/ancienneté,
scoring Optimum, puis tri 🆕 récentes → 👥 RH/SIRH → ⭐ score → date. Le résultat
est envoyé par e-mail (SMTP Gmail). Conçu pour tourner via GitHub Actions (cron).

Variables d'environnement attendues :
  FT_CLIENT_ID, FT_CLIENT_SECRET, ADZUNA_APP_ID, ADZUNA_APP_KEY, JOOBLE_API_KEY
  MAIL_USER (adresse Gmail expéditrice), MAIL_PASS (mot de passe d'application),
  MAIL_TO  (destinataire ; défaut = MAIL_USER)
Optionnel : JOURS_MAX (défaut 14), MAX_OFFRES (défaut 50), MAX_MAIL (défaut 80).
"""
import os
import re
import sys
import html
import json
import base64
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import recherche_offres as ro
import sites_carrieres as sc

BREVO_URL = "https://api.brevo.com/v3/smtp/email"

load_dotenv(override=True)

JOURS_MAX = int(os.getenv("JOURS_MAX", "14"))
MAX_OFFRES = int(os.getenv("MAX_OFFRES", "200"))
MAX_MAIL = int(os.getenv("MAX_MAIL", "120"))
TYPE_CONTRAT = "CDI"


def _inclusions_mots():
    """INCLUSIONS_TITRE_PAR_DEFAUT sans le suffixe « :couleur »."""
    mots = []
    for ligne in ro.INCLUSIONS_TITRE_PAR_DEFAUT:
        mot, sep, _coul = ligne.rpartition(":")
        mots.append(mot.strip() if sep and mot.strip() else ligne.strip())
    return mots


def collecter_brutes():
    """Toutes les variantes : FT + Adzuna + Jooble + sites carrières. -> (offres, erreurs)."""
    cid, secret = os.getenv("FT_CLIENT_ID"), os.getenv("FT_CLIENT_SECRET")
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    jooble_key = os.getenv("JOOBLE_API_KEY")
    brutes, erreurs = [], []

    token = None
    if cid and secret:
        try:
            token = ro.obtenir_token(cid, secret)
        except Exception as e:  # noqa: BLE001
            erreurs.append(f"France Travail (auth) : {e}")

    for variante in ro.SYNONYMES_PAR_DEFAUT:
        if token:
            try:
                cfg = dict(ro.CONFIG, mots_cles=variante, departement=None,
                           type_contrat=TYPE_CONTRAT, max_offres=MAX_OFFRES,
                           jours_max=JOURS_MAX)
                for o in ro.rechercher(token, cfg):
                    o["_source"], o["_variante"] = "France Travail", variante
                    brutes.append(o)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"France Travail « {variante} » : {e}")
        if app_id and app_key:
            try:
                cfg = dict(ro.CONFIG, mots_cles=variante, type_contrat=TYPE_CONTRAT,
                           max_offres=MAX_OFFRES, where_adzuna=None, jours_max=JOURS_MAX)
                for o in ro.rechercher_adzuna(cfg, app_id, app_key):
                    o["_source"], o["_variante"] = "Adzuna", variante
                    brutes.append(o)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"Adzuna « {variante} » : {e}")
            # Autour du domicile : la recherche nationale plafonne a 200 resultats
            # (surtout Paris) et noie la region. Jooble ne sait pas chercher par
            # ville francaise (renvoie des offres US) : Adzuna seul. +7 appels/run.
            try:
                cfg = dict(ro.CONFIG, mots_cles=variante, type_contrat=TYPE_CONTRAT,
                           max_offres=MAX_OFFRES, where_adzuna=ro.DOMICILE,
                           distance_adzuna=ro.RAYON_DOMICILE_KM, jours_max=JOURS_MAX)
                for o in ro.rechercher_adzuna(cfg, app_id, app_key):
                    o["_source"], o["_variante"] = "Adzuna", f"{variante} (autour de {ro.DOMICILE})"
                    brutes.append(o)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"Adzuna autour de {ro.DOMICILE} « {variante} » : {e}")
        if jooble_key:
            try:
                cfg = dict(ro.CONFIG, mots_cles=variante, max_offres=MAX_OFFRES,
                           location_jooble=None)
                for o in ro.rechercher_jooble(cfg, jooble_key):
                    o["_source"], o["_variante"] = "Jooble", variante
                    brutes.append(o)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"Jooble « {variante} » : {e}")
    # Sites carrières des grands groupes (Workday, LVMH, BPCE) : API publiques sans
    # clé, toutes les variantes interrogées EN PARALLÈLE (minutes Actions comptées).
    cfg = dict(ro.CONFIG, max_offres=MAX_OFFRES, jours_max=JOURS_MAX,
               inclure_titres=_inclusions_mots(),
               exclure_titres=ro.EXCLUSIONS_TITRE_PAR_DEFAUT)
    offres, errs = sc.rechercher_sites_carrieres(cfg, ro.SYNONYMES_PAR_DEFAUT)
    brutes += offres
    erreurs += errs
    return brutes, erreurs


def decorer_france(brutes):
    """Filtre + isole le pool France + décore (score, rh, po, favori, recent) + tri."""
    gardees, _exclues, stats = ro.filtrer(
        brutes, JOURS_MAX, ro.EXCLUSIONS_TITRE_PAR_DEFAUT, _inclusions_mots())
    francaises = [o for o in gardees
                  if o.get("_source") != "jobup.ch"
                  and not ro.est_international(o) and not ro.est_suisse(o)]
    favoris = set(ro.DEPTS_FAVORIS_PAR_DEFAUT)
    seuil_recent = (date.today() - timedelta(days=1)).isoformat()
    items = []
    for o in francaises:
        sc, comp = ro.score_optimum(o, ro.PROFIL_OPTIMUM)
        d = (o.get("dateCreation", "") or "")[:10]
        items.append({
            "o": o, "score": sc,
            "recent": bool(d) and d >= seuil_recent,
            "rh": ro.est_domaine_rh(o),
            "po": ro.est_product_owner(o),
            "favori": ro.est_departement_favori(o, favoris),
        })
    items.sort(key=lambda it: (it["recent"], bool(it["rh"]), it["score"],
                               (it["o"].get("dateCreation", "") or "")[:10]),
               reverse=True)
    return items, stats


FONT = "font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"

# Palette « mode sombre » du mail. Les lignes colorées par métier gardent leurs
# couleurs vives ; seules les zones neutres (fond, lignes sans couleur, textes)
# passent en sombre.
FOND_PAGE = "#14161b"      # fond de page
FOND_LIGNE = "#272b33"     # ligne d'offre neutre (sans couleur métier)
TXT_CLAIR = "#e7e9ee"      # texte principal clair
TXT_DOUX = "#aab2bf"       # texte secondaire (badges sur ligne neutre)
TXT_FAIBLE = "#8b94a1"     # légendes / sous-titres / en-têtes de jour

# Coloriage de ligne par métier, IDENTIQUE à l'app (liste) : on lit la syntaxe
# « mot:couleur » de INCLUSIONS_TITRE_PAR_DEFAUT (owner/PM → orange, analyst/
# business/amoa/fonctionnel → bleu) et on colore la ligne entière comme dans la liste.
COULEURS = {
    "violet": "#8e5bd0", "bleu": "#3b7dd8", "blue": "#3b7dd8",
    "vert": "#2fa564", "green": "#2fa564", "orange": "#dd7a1c",
    "rouge": "#d24545", "red": "#d24545", "jaune": "#c29a1c", "yellow": "#c29a1c",
    "rose": "#cc4a96", "pink": "#cc4a96", "cyan": "#1f9595",
    "gris": "#6f6f6f", "gray": "#6f6f6f",
}


def _couleurs_mot():
    m = {}
    for ligne in ro.INCLUSIONS_TITRE_PAR_DEFAUT:
        mot, sep, coul = ligne.rpartition(":")
        coul = coul.strip().lower()
        if sep and mot.strip() and coul in COULEURS:
            m[mot.strip()] = COULEURS[coul]
    return m


COULEURS_MOT = _couleurs_mot()
AUJ = date.today().isoformat()
HIER = (date.today() - timedelta(days=1)).isoformat()


def _couleur_ligne(o):
    """1re couleur (mot:couleur) trouvée dans le titre, comme dans la liste de l'app."""
    titre = ro.normaliser(o.get("intitule", ""))
    for mot, hexa in COULEURS_MOT.items():
        if ro.normaliser(mot) in titre:
            return hexa
    return None


def _rang_date(o):
    d = (o.get("dateCreation", "") or "")[:10]
    if d == AUJ:
        return 0
    if d == HIER:
        return 1
    return 2


def _bucket(o):
    d = (o.get("dateCreation", "") or "")[:10]
    if d == AUJ:
        return "📅 Aujourd'hui"
    if d == HIER:
        return "📅 Hier"
    return f"📅 {d}" if d else "📅 Date inconnue"


def _dt_paris_offre(iso):
    """Date d'offre (ISO, sources variées) -> datetime heure de Paris (ou None)."""
    if not iso:
        return None
    s = str(iso).strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)   # tronque les fractions > 6 chiffres (Jooble)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:                   # sans fuseau -> supposé UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Europe/Paris"))


def _fmt_date_heure(iso):
    """« JJ/MM HHhMM » si l'heure est réellement fournie, sinon « JJ/MM » (heure de Paris)."""
    if not iso:
        return ""
    s = str(iso).strip()
    heure_brute = s.split("T", 1)[1][:5] if "T" in s else ""
    a_heure = bool(heure_brute) and heure_brute != "00:00"
    dt = _dt_paris_offre(s)
    if dt is None:
        return s[:10]
    return dt.strftime("%d/%m %Hh%M") if a_heure else dt.strftime("%d/%m")


def _ligne_offre(it):
    """Une offre = une ligne pleine colorée par métier (comme la liste de l'app)."""
    o = it["o"]
    titre = html.escape(o.get("intitule", "Sans titre"))
    ent = html.escape(ro.nom_entreprise(o) or "Entreprise non précisée")
    lieu = html.escape((o.get("lieuTravail") or {}).get("libelle", "") or "")
    d = _fmt_date_heure(o.get("dateCreation"))
    url = html.escape((o.get("origineOffre") or {}).get("urlOrigine", "") or "")
    bg = _couleur_ligne(o)
    fond = bg or FOND_LIGNE
    txt = "#ffffff" if bg else TXT_CLAIR
    sub = "rgba(255,255,255,.88)" if bg else TXT_DOUX

    bdg = "🆕 " if it["recent"] else ""
    if it["favori"]:
        bdg += "🎯 "
    if it["rh"]:
        bdg += "👥 "
    if it["score"]:
        bdg += f"⭐{it['score']} "
    if lieu:
        bdg += f"📍{lieu} "
    if d:
        bdg += f"📅{d} "
    titre_html = (f'<a href="{url}" style="color:{txt};font-weight:bold;'
                  f'text-decoration:none">{titre}</a>' if url
                  else f'<b style="color:{txt}">{titre}</b>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:separate;margin:0 0 6px"><tr>'
            f'<td style="background:{fond};border-radius:8px;padding:9px 13px;{FONT}'
            f'font-size:14px;line-height:1.45;color:{txt}">'
            f'<span style="font-size:12px;color:{sub}">{bdg}</span>· {titre_html}'
            f'<span style="color:{sub}"> — {ent}</span></td></tr></table>')


def _section(titre, sous_titre, items, vide):
    """Section avec en-têtes de jour (Aujourd'hui / Hier / date), comme la liste."""
    if not items:
        corps = f'<p style="color:{TXT_FAIBLE};font-size:14px;{FONT}">{vide}</p>'
    else:
        parts, prev = [], None
        for it in items:
            b = _bucket(it["o"])
            if b != prev:
                parts.append(f'<div style="font-size:13px;font-weight:700;color:{TXT_DOUX};'
                             f'margin:12px 0 5px;{FONT}">{b}</div>')
                prev = b
            parts.append(_ligne_offre(it))
        corps = "".join(parts)
    return (f'<h2 style="font-size:17px;margin:22px 0 2px;color:{TXT_CLAIR};{FONT}">{titre}</h2>'
            f'<p style="font-size:12px;color:{TXT_FAIBLE};margin:0 0 6px;{FONT}">{sous_titre}</p>'
            f'{corps}')


def _pill(txt):
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:11px;'
            f'background:rgba(255,255,255,.20);color:#fff;font-size:13px;'
            f'margin:3px 6px 0 0">{txt}</span>')


def construire_html(items, stats, erreurs):
    """Mail = 💎 Perfect + 🎯 France RÉCENTES, rendu « liste de l'app » (lignes colorées)."""
    aujourd = date.today().isoformat()
    perfect = [it for it in items if it["score"] > 2 and it["rh"] and it["favori"]]
    pids = {id(it) for it in perfect}
    recents = [it for it in items if it["recent"] and id(it) not in pids][:MAX_MAIL]
    # Tri identique à l'app : par tranche jour, puis 👥 RH en tête, puis score.
    tri = lambda lst: sorted(lst, key=lambda it: (_rang_date(it["o"]),
                                                  0 if it["rh"] else 1, -it["score"]))
    perfect, recents = tri(perfect), tri(recents)
    n_envoye = len(perfect) + len(recents)

    err_html = ""
    if erreurs:
        err_html = (f'<div style="background:#3a2020;border:1px solid #6e3a3a;'
                    f'color:#f0b3b3;border-radius:8px;padding:10px 14px;font-size:13px;'
                    f'margin:8px 0 0;{FONT}">⚠️ Sources en erreur : '
                    + html.escape("; ".join(erreurs)) + "</div>")

    nfav = sum(1 for it in (perfect + recents) if it["favori"])
    nrh = sum(1 for it in (perfect + recents) if it["rh"])
    entete = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="background:#1f6feb;border-radius:12px;padding:18px 20px">'
        f'<div style="font-size:21px;font-weight:800;color:#fff;{FONT}">'
        f'🔎 Offres du {aujourd}</div><div style="margin-top:8px">'
        + _pill(f"📋 {n_envoye} offres") + _pill(f"💎 {len(perfect)} Perfect")
        + _pill(f"🆕 {len(recents)} récentes") + _pill(f"🎯 {nfav} favoris")
        + _pill(f"👥 {nrh} RH/SIRH")
        + '</div></td></tr></table>')

    corps = f"""<!DOCTYPE html><html><body style="margin:0;background:{FOND_PAGE};padding:18px 10px;color:{TXT_CLAIR};{FONT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{FOND_PAGE}" style="background:{FOND_PAGE}"><tr><td align="center" style="background:{FOND_PAGE}">
<table role="presentation" width="660" cellpadding="0" cellspacing="0" style="max-width:660px;width:100%">
<tr><td>
{entete}
{err_html}
{_section(f"💎 Perfect ({len(perfect)})",
          "Score &gt; 2 ⭐ · 👥 RH/SIRH · 🎯 département favori", perfect,
          "Aucune offre Perfect aujourd'hui.")}
{_section(f"🎯 France — récentes ({len(recents)})",
          "Business Analyst en bleu · Product Owner en orange (comme la liste de l'app)",
          recents, "Aucune offre récente aujourd'hui.")}
<p style="color:{TXT_FAIBLE};font-size:12px;text-align:center;margin:22px 0 6px;{FONT}">
Recherche automatique quotidienne · offres récentes + Perfect</p>
</td></tr></table>
</td></tr></table>
</body></html>"""
    return corps, n_envoye


def envoyer(html_corps, n):
    """Envoi via l'API transactionnelle Brevo (pas de SMTP/2FA)."""
    api_key = os.getenv("BREVO_API_KEY")
    sender = os.getenv("MAIL_FROM") or os.getenv("MAIL_USER")
    dest = os.getenv("MAIL_TO") or sender
    if not api_key or not sender:
        print("BREVO_API_KEY / MAIL_FROM manquants : pas d'envoi.", file=sys.stderr)
        return False
    payload = {
        "sender": {"email": sender, "name": "Recherche emploi"},
        "to": [{"email": d.strip()} for d in dest.split(",") if d.strip()],
        "subject": f"🔎 {n} offres — {date.today().isoformat()}",
        "htmlContent": html_corps,
    }
    r = requests.post(BREVO_URL, json=payload, timeout=30,
                      headers={"api-key": api_key, "accept": "application/json",
                               "content-type": "application/json"})
    if r.status_code >= 300:
        print(f"Echec Brevo {r.status_code} : {r.text[:300]}", file=sys.stderr)
        return False
    print(f"E-mail envoyé à {dest} ({n} offres) via Brevo.")
    return True


# --- Verrou anti-doublon de l'e-mail quotidien -----------------------------
# La fenêtre d'envoi est large (17h→06h Paris, voir main) pour résister aux runs
# GitHub retardés ; ce verrou garantit qu'UN SEUL e-mail part par jour. On mémorise
# la date (Paris) du dernier envoi dans un petit fichier, lu/écrit via l'API GitHub
# avec le GITHUB_TOKEN du workflow (env GH_API_TOKEN). Stocké sur une branche
# DÉDIÉE `mail-state` (créée au besoin) — surtout PAS `data`, que le snapshot
# force-push et écraserait toutes les 4 h.
# GITHUB_REPOSITORY (« propriétaire/nom ») est fourni automatiquement par GitHub Actions :
# une copie du projet dans un autre dépôt utilise donc SON propre dépôt.
REPO_API = ("https://api.github.com/repos/"
            + os.getenv("GITHUB_REPOSITORY", "dylanhatte/recherche-emploi"))
LOCK_BRANCH = "mail-state"
LOCK_API = f"{REPO_API}/contents/mail_lock.json"


def _lock_headers(token):
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def lire_verrou(token):
    """(date du dernier envoi, sha) depuis `mail-state` ; (None, None) si absent."""
    try:
        r = requests.get(LOCK_API + f"?ref={LOCK_BRANCH}",
                         headers=_lock_headers(token), timeout=20)
        if r.status_code != 200:                 # 404 = branche/fichier pas encore créés
            return None, None
        meta = r.json()
        contenu = base64.b64decode(meta.get("content", "")).decode("utf-8")
        d = json.loads(contenu) if contenu.strip() else {}
        return d.get("dernier_envoi"), meta.get("sha")
    except Exception as e:  # noqa: BLE001
        print("[!] lecture verrou :", e)
        return None, None


def _assurer_branche(token):
    """Crée la branche `mail-state` si elle n'existe pas (depuis la branche par défaut)."""
    try:
        r = requests.get(f"{REPO_API}/git/refs/heads/{LOCK_BRANCH}",
                         headers=_lock_headers(token), timeout=20)
        if r.status_code == 200:
            return True
        repo = requests.get(REPO_API, headers=_lock_headers(token), timeout=20).json()
        base = repo.get("default_branch", "main")
        rb = requests.get(f"{REPO_API}/git/refs/heads/{base}",
                         headers=_lock_headers(token), timeout=20)
        if rb.status_code != 200:
            return False
        cr = requests.post(f"{REPO_API}/git/refs", headers=_lock_headers(token), timeout=20,
                          json={"ref": f"refs/heads/{LOCK_BRANCH}",
                                "sha": rb.json()["object"]["sha"]})
        return cr.status_code in (200, 201)
    except Exception as e:  # noqa: BLE001
        print("[!] creation branche verrou :", e)
        return False


def poser_verrou(token, jour, sha):
    """Écrit la date d'envoi sur `mail-state`. -> True si OK."""
    if not _assurer_branche(token):
        print("[!] branche verrou indisponible — verrou non posé.")
        return False
    body = {"message": f"mail envoye {jour}", "branch": LOCK_BRANCH,
            "content": base64.b64encode(
                json.dumps({"dernier_envoi": jour}).encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(LOCK_API, headers=_lock_headers(token), json=body, timeout=25)
        if r.status_code in (200, 201):
            return True
        print(f"[!] pose verrou : HTTP {r.status_code} {r.text[:150]}")
        return False
    except Exception as e:  # noqa: BLE001
        print("[!] pose verrou :", e)
        return False


SNAPSHOT_AGE_MAX_H = 10   # au-delà (snapshots GitHub en retard/abandonnés) : collecte en direct
HEURE_ENVOI = 17          # début du créneau d'envoi (heure de Paris) ; à aligner avec le
                          # tri shell de .github/workflows/daily-search.yml


def lire_snapshot(token):
    """Dernier instantané (branche `data`) -> dict, ou None s'il est illisible.

    L'e-mail réutilise les offres du snapshot (publié toutes les 4 h) au lieu de
    tout re-télécharger : ~3 min de GitHub Actions en moins par envoi, et un
    contenu identique à celui de l'app. Contient aussi les « sources muettes ».
    """
    try:
        r = requests.get(f"{REPO_API}/contents/offres_snapshot.json?ref=data", timeout=60,
                         headers={**_lock_headers(token), "Accept": "application/vnd.github.raw"})
        return r.json() if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001
        print("[!] lecture snapshot :", e)
        return None


def offres_du_jour(token):
    """(offres brutes, erreurs) : snapshot récent si possible, sinon collecte en direct."""
    snap = lire_snapshot(token) if token else None
    alertes = [f"{s} : muette depuis 3 passages du snapshot (site modifié ?)"
               for s in (snap or {}).get("sources_muettes") or []]
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(snap["genere_le"])).total_seconds() / 3600
    except (TypeError, KeyError, ValueError):
        age_h = None
    if age_h is not None and age_h <= SNAPSHOT_AGE_MAX_H and snap.get("offres"):
        print(f"Instantané du {snap['genere_le']} utilisé ({age_h:.1f} h).")
        return snap["offres"], list(snap.get("erreurs") or []) + alertes
    print("Instantané " + (f"trop ancien ({age_h:.1f} h)" if age_h is not None else "illisible")
          + " : collecte en direct.")
    brutes, erreurs = collecter_brutes()
    return brutes, erreurs + alertes


def soiree_envoi(maintenant):
    """Date (AAAA-MM-JJ) de la « soirée » d'envoi si `maintenant` (heure de Paris)
    tombe dans le créneau [HEURE_ENVOI, 06h00[, sinon None. Un run à 01h compte
    pour la veille : c'est la clé du verrou « un seul e-mail par soirée »."""
    if 6 <= maintenant.hour < HEURE_ENVOI:
        return None
    return (maintenant - timedelta(hours=6)).date().isoformat()


def main():
    # Garde-fou « dès 17h Paris toute l'année » (souhait de l'utilisateur, oct. 2026),
    # robuste aux retards GitHub. Les runs planifiés arrivent souvent avec 2 à 5 h
    # de retard : une fenêtre étroite les rejetait presque tous (≈ 1 mail/semaine).
    # On accepte donc 17h → 06h du matin et on envoie dès qu'un run y tombe ; le
    # workflow déclenche dès 15h pour qu'un run retardé tombe vers 17h. Le verrou est
    # indexé sur la « soirée » (un run à 01h compte pour la veille), ce qui garantit
    # UN SEUL envoi par soirée. Le mail part au plus tôt à 17h, parfois plus tard.
    # Actif seulement si CHECK_CRENEAU est défini ; un lancement manuel passe outre.
    token = os.getenv("GH_API_TOKEN")
    jour = sha = None
    if os.getenv("CHECK_CRENEAU"):
        now = datetime.now(ZoneInfo("Europe/Paris"))
        jour = soiree_envoi(now)
        if jour is None:
            print(f"Hors fenêtre d'envoi (il est {now.hour}h à Paris) — run ignoré.")
            return
        if token:
            dernier, sha = lire_verrou(token)
            if dernier == jour:
                print(f"E-mail déjà envoyé pour la soirée du {jour} — run ignoré.")
                return
        else:
            print("[!] GH_API_TOKEN absent : envoi sans verrou anti-doublon.")

    brutes, erreurs = offres_du_jour(token)
    items, stats = decorer_france(brutes)
    print(f"{stats['brutes']} brutes -> {len(items)} offres France.")
    for e in erreurs:
        print(" [!]", e)
    corps, n_envoye = construire_html(items, stats, erreurs)
    print(f"E-mail : {n_envoye} offres (Perfect + recentes).")
    ok = envoyer(corps, n_envoye)
    # Verrou posé UNIQUEMENT après un envoi réussi : si l'envoi échoue, un cron
    # suivant du même créneau réessaiera (et ne posera pas de faux verrou).
    if ok and jour and token:
        poser_verrou(token, jour, sha)


if __name__ == "__main__":
    main()
