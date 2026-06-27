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
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import recherche_offres as ro

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
    """Lance toutes les variantes sur FT + Adzuna + Jooble. Retourne (offres, erreurs)."""
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
        if jooble_key:
            try:
                cfg = dict(ro.CONFIG, mots_cles=variante, max_offres=MAX_OFFRES,
                           location_jooble=None)
                for o in ro.rechercher_jooble(cfg, jooble_key):
                    o["_source"], o["_variante"] = "Jooble", variante
                    brutes.append(o)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"Jooble « {variante} » : {e}")
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
            "rh": ro.comprend_rh(comp),
            "po": ro.est_product_owner(o),
            "favori": ro.est_departement_favori(o, favoris),
        })
    items.sort(key=lambda it: (it["recent"], bool(it["rh"]), it["score"],
                               (it["o"].get("dateCreation", "") or "")[:10]),
               reverse=True)
    return items, stats


FONT = "font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"

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
    fond = bg or "#eceef2"
    txt = "#ffffff" if bg else "#1f2430"
    sub = "rgba(255,255,255,.88)" if bg else "#5b6472"

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
        corps = f'<p style="color:#9aa0aa;font-size:14px;{FONT}">{vide}</p>'
    else:
        parts, prev = [], None
        for it in items:
            b = _bucket(it["o"])
            if b != prev:
                parts.append(f'<div style="font-size:13px;font-weight:700;color:#6b7280;'
                             f'margin:12px 0 5px;{FONT}">{b}</div>')
                prev = b
            parts.append(_ligne_offre(it))
        corps = "".join(parts)
    return (f'<h2 style="font-size:17px;margin:22px 0 2px;color:#111;{FONT}">{titre}</h2>'
            f'<p style="font-size:12px;color:#9aa0aa;margin:0 0 6px;{FONT}">{sous_titre}</p>'
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
        err_html = (f'<div style="background:#fff4f4;border:1px solid #f3c2c2;'
                    f'color:#a23;border-radius:8px;padding:10px 14px;font-size:13px;'
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

    corps = f"""<!DOCTYPE html><html><body style="margin:0;background:#eef0f4;padding:18px 10px;{FONT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
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
<p style="color:#9aa0aa;font-size:12px;text-align:center;margin:22px 0 6px;{FONT}">
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


def main():
    # Garde-fou « ~20h Paris toute l'année ». Le cron GitHub est en UTC (ignore
    # l'heure d'été) ET souvent retardé, surtout en haut de l'heure. On planifie
    # donc deux crons en heure creuse (17:55 et 18:55 UTC) et on n'envoie que dans
    # une FENÊTRE bornée 19h55→20h45 Paris : un seul cron y tombe par saison, et la
    # borne haute empêche tout envoi qui dériverait vers 21h en cas de gros retard.
    # Actif seulement quand CHECK_20H est défini ; un lancement manuel passe outre.
    if os.getenv("CHECK_20H"):
        now = datetime.now(ZoneInfo("Europe/Paris"))
        minutes = now.hour * 60 + now.minute
        if not (19 * 60 + 55 <= minutes < 20 * 60 + 45):   # [19h55, 20h45[
            print(f"Hors fenêtre d'envoi (il est {now:%Hh%M} à Paris) — run ignoré.")
            return

    brutes, erreurs = collecter_brutes()
    items, stats = decorer_france(brutes)
    print(f"{stats['brutes']} brutes -> {len(items)} offres France.")
    for e in erreurs:
        print(" [!]", e)
    corps, n_envoye = construire_html(items, stats, erreurs)
    print(f"E-mail : {n_envoye} offres (Perfect + recentes).")
    envoyer(corps, n_envoye)


if __name__ == "__main__":
    main()
