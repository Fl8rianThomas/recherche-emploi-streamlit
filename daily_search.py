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
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import date, timedelta

from dotenv import load_dotenv

import recherche_offres as ro

load_dotenv(override=True)

JOURS_MAX = int(os.getenv("JOURS_MAX", "14"))
MAX_OFFRES = int(os.getenv("MAX_OFFRES", "50"))
MAX_MAIL = int(os.getenv("MAX_MAIL", "80"))
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


def construire_html(items, stats, erreurs):
    aujourd = date.today().isoformat()
    nrec = sum(1 for it in items if it["recent"])
    nfav = sum(1 for it in items if it["favori"])
    nrh = sum(1 for it in items if it["rh"])
    lignes = []
    for it in items[:MAX_MAIL]:
        o = it["o"]
        titre = html.escape(o.get("intitule", "Sans titre"))
        ent = html.escape(ro.nom_entreprise(o) or "Entreprise non précisée")
        lieu = html.escape((o.get("lieuTravail") or {}).get("libelle", "") or "-")
        d = (o.get("dateCreation", "") or "")[:10]
        url = (o.get("origineOffre") or {}).get("urlOrigine", "")
        src = html.escape(o.get("_source", ""))
        badges = "".join([
            "🆕 " if it["recent"] else "",
            "🎯 " if it["favori"] else "",
            "🟣 " if it["po"] else "",
            "👥 " if it["rh"] else "",
            f"⭐{it['score']} " if it["score"] else "",
        ])
        lien = f'<a href="{html.escape(url)}">{titre}</a>' if url else titre
        lignes.append(
            f'<li style="margin:0 0 10px"><b>{badges}{lien}</b><br>'
            f'<span style="color:#555">{ent} · {lieu} · {d} · {src}</span></li>')
    err_html = ""
    if erreurs:
        err_html = ("<p style='color:#b00'>⚠️ Sources en erreur : "
                    + html.escape("; ".join(erreurs)) + "</p>")
    tronq = ("" if len(items) <= MAX_MAIL
             else f"<p><i>… et {len(items) - MAX_MAIL} autres "
                  f"(voir l'app pour la liste complète).</i></p>")
    return f"""<html><body style="font-family:Arial,sans-serif;font-size:14px">
<h2>🔎 Offres du {aujourd}</h2>
<p><b>{len(items)}</b> offres France retenues
({stats['brutes']} brutes) · 🆕 {nrec} récentes · 🎯 {nfav} favoris · 👥 {nrh} RH/SIRH</p>
{err_html}
<ol>{''.join(lignes)}</ol>
{tronq}
<p style="color:#888">Recherche automatique — onglet France/Optimum.</p>
</body></html>"""


def envoyer(html_corps, n):
    user, pwd = os.getenv("MAIL_USER"), os.getenv("MAIL_PASS")
    dest = os.getenv("MAIL_TO") or user
    if not user or not pwd:
        print("MAIL_USER / MAIL_PASS manquants : pas d'envoi.", file=sys.stderr)
        return False
    msg = MIMEText(html_corps, "html", "utf-8")
    msg["Subject"] = f"🔎 {n} offres — {date.today().isoformat()}"
    msg["From"], msg["To"] = user, dest
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.sendmail(user, [d.strip() for d in dest.split(",")], msg.as_string())
    print(f"E-mail envoyé à {dest} ({n} offres).")
    return True


def main():
    brutes, erreurs = collecter_brutes()
    items, stats = decorer_france(brutes)
    print(f"{stats['brutes']} brutes -> {len(items)} offres France.")
    for e in erreurs:
        print(" [!]", e)
    envoyer(construire_html(items, stats, erreurs), len(items))


if __name__ == "__main__":
    main()
