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
import sys
import html
from datetime import date, timedelta

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


# Palette (cohérente avec l'app) : barre de couleur par métier.
ACCENT_PO = "#8e5bd0"   # violet — Product Owner / Manager
ACCENT_BA = "#3b7dd8"   # bleu — Business Analyst / AMOA / autre
FONT = ("font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif")


def _chip(txt, bg):
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:11px;'
            f'font-size:11px;font-weight:600;color:#fff;background:{bg};'
            f'margin:2px 4px 2px 0">{txt}</span>')


def _carte(it):
    """Une offre = une carte (table) avec barre de couleur, badges, score, méta."""
    o = it["o"]
    titre = html.escape(o.get("intitule", "Sans titre"))
    ent = html.escape(ro.nom_entreprise(o) or "Entreprise non précisée")
    lieu = html.escape((o.get("lieuTravail") or {}).get("libelle", "") or "-")
    d = (o.get("dateCreation", "") or "")[:10]
    url = html.escape((o.get("origineOffre") or {}).get("urlOrigine", ""))
    src = html.escape(o.get("_source", ""))
    accent = ACCENT_PO if it["po"] else ACCENT_BA

    chips = ""
    if it["recent"]:
        chips += _chip("🆕 Nouveau", "#2fa564")
    if it["favori"]:
        chips += _chip("🎯 Favori", "#dd7a1c")
    if it["rh"]:
        chips += _chip("👥 RH/SIRH", "#1f9595")
    if it["po"]:
        chips += _chip("Product Owner", ACCENT_PO)
    chips_html = (f'<div style="margin:6px 0 2px">{chips}</div>' if chips else "")

    score_pill = ""
    if it["score"]:
        score_pill = (f'<span style="display:inline-block;padding:2px 10px;'
                      f'border-radius:12px;background:#fbe7a2;color:#5a4500;'
                      f'font-size:13px;font-weight:700;white-space:nowrap">'
                      f'⭐ {it["score"]}</span>')
    titre_html = (f'<a href="{url}" style="color:#13366e;text-decoration:none">{titre}</a>'
                  if url else titre)
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="border-collapse:separate;margin:0 0 10px">
 <tr><td style="background:#ffffff;border:1px solid #e7e9ee;border-left:4px solid {accent};
   border-radius:8px;padding:12px 16px">
   <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
     <td style="font-size:15px;font-weight:bold;line-height:1.35;{FONT}">{titre_html}</td>
     <td align="right" valign="top" style="padding-left:10px">{score_pill}</td>
   </tr></table>
   {chips_html}
   <div style="color:#6b7280;font-size:13px;margin-top:5px;{FONT}">
     {ent} &middot; {lieu} &middot; {d} &middot; {src}</div>
 </td></tr>
</table>"""


def _section(titre, sous_titre, items, vide):
    corps = ("".join(_carte(it) for it in items) if items
             else f'<p style="color:#9aa0aa;font-size:14px;{FONT}">{vide}</p>')
    return (f'<h2 style="font-size:17px;margin:24px 0 4px;color:#111;{FONT}">{titre}</h2>'
            f'<p style="font-size:12px;color:#9aa0aa;margin:0 0 12px;{FONT}">{sous_titre}</p>'
            f'{corps}')


def _pill_entete(txt):
    return (f'<span style="display:inline-block;padding:3px 11px;border-radius:13px;'
            f'background:rgba(255,255,255,.18);color:#fff;font-size:13px;'
            f'margin:3px 6px 0 0">{txt}</span>')


def construire_html(items, stats, erreurs):
    """Mail = 💎 Perfect + 🎯 France RÉCENTES uniquement (pas les plus anciennes)."""
    aujourd = date.today().isoformat()
    # Perfect : score > 2 ET RH/SIRH ET département favori (cf. onglet Perfect).
    perfect = [it for it in items if it["score"] > 2 and it["rh"] and it["favori"]]
    pids = {id(it) for it in perfect}
    # France récentes : uniquement la partie « 🆕 » (on exclut les plus anciennes),
    # sans répéter celles déjà listées en Perfect.
    recents = [it for it in items if it["recent"] and id(it) not in pids][:MAX_MAIL]
    n_envoye = len(perfect) + len(recents)

    err_html = ""
    if erreurs:
        err_html = (f'<div style="background:#fff4f4;border:1px solid #f3c2c2;'
                    f'color:#a23;border-radius:8px;padding:10px 14px;font-size:13px;'
                    f'margin:0 0 14px;{FONT}">⚠️ Sources en erreur : '
                    + html.escape("; ".join(erreurs)) + "</div>")

    entete = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
              f'<tr><td style="background:#1f6feb;border-radius:12px;padding:20px 22px">'
              f'<div style="font-size:22px;font-weight:800;color:#fff;{FONT}">'
              f'🔎 Offres du {aujourd}</div>'
              f'<div style="margin-top:8px">'
              + _pill_entete(f"💎 {len(perfect)} Perfect")
              + _pill_entete(f"🆕 {len(recents)} récentes")
              + _pill_entete(f"{stats['brutes']} analysées")
              + '</div></td></tr></table>')

    corps = f"""<!DOCTYPE html><html><body style="margin:0;background:#eef0f4;padding:18px 10px;{FONT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%">
<tr><td>
{entete}
<div style="height:10px"></div>
{err_html}
{_section(f"💎 Perfect ({len(perfect)})",
          "Score &gt; 2 ⭐ · 👥 RH/SIRH · 🎯 département favori", perfect,
          "Aucune offre Perfect aujourd'hui.")}
{_section(f"🎯 France — récentes ({len(recents)})",
          "Offres du jour · Product Owner en violet, Business Analyst en bleu",
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
