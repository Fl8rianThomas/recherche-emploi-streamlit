"""Tests des fonctions pures (sans réseau) : filtres, lieux, dates, groupes.

Lancer : python -m unittest discover -s tests -v
Chaque cas reprend un piège réellement rencontré (voir les commentaires).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import recherche_offres as ro  # noqa: E402
import sites_carrieres as sc   # noqa: E402
import refresh_snapshot as rs  # noqa: E402
import daily_search as ds      # noqa: E402


def offre(titre="Business Analyst", entreprise="Acme", lieu="", description="", date=""):
    return {"intitule": titre, "entreprise": {"nom": entreprise},
            "lieuTravail": {"libelle": lieu}, "description": description,
            "dateCreation": date}


class TestExclusions(unittest.TestCase):
    R = ro.regles_exclusion(ro.EXCLUSIONS_TITRE_PAR_DEFAUT)

    def exclu(self, titre):
        return ro.terme_exclu(ro.normaliser(titre), self.R)

    def test_data_analyst_exclu_sauf_poste_mixte(self):
        self.assertEqual(self.exclu("Data Analyst H/F"), "data analyst")
        self.assertIsNone(self.exclu("Business Data Analyst"))
        self.assertIsNone(self.exclu("Data Analyst / Business Analyst"))

    def test_stage_alternance_exclus(self):
        self.assertEqual(self.exclu("Stage - Product Owner"), "stage")
        self.assertEqual(self.exclu("Alternance Business Analyst"), "alternance")

    def test_ba_garde(self):
        self.assertIsNone(self.exclu("Business Analyst Senior SIRH"))

    def test_syntaxe_sauf(self):
        regles = ro.regles_exclusion(["Chef de projet sauf AMOA, fonctionnel"])
        self.assertEqual(regles, [("chef de projet", ["amoa", "fonctionnel"])])


class TestDomaineRH(unittest.TestCase):
    """Audit du 2026-10-14 : faux positifs RH relevés sur les offres réelles."""
    def test_faux_positifs(self):
        for desc in ("Poste basé en Auvergne-Rhône-Alpes", "dans le Bas-Rhin",
                     "une vingtaine de collaborateurs",            # « gta » dans vingtaine
                     "le vœu le plus cher de Christian Dior",     # « hris » dans christian
                     "Entretien RH puis entretien manager", "un RH de proximité t'accompagnera",
                     "contacté par l'équipe RH", "une politique RH axée formation",
                     "27 jours de congés payés, RTT"):            # « payes » ≠ paye
            o = offre("Business Analyst", description=desc)
            self.assertFalse(ro.est_domaine_rh(o), desc)
            self.assertEqual(ro.score_optimum(o, ["rh:5", "gta:5", "hris:5", "paye:3"])[1], [], desc)

    def test_vrais_rh(self):
        cas = [offre("Analyste fonctionnel SAP HR"), offre("AMOA SIRH & paye"),
               offre("Business Analyst", description="Évolution des outils RH du groupe"),
               offre("Product Owner", description="outils internes dédiés aux ressources humaines"),
               offre("Business Analyst", description="projet de transformation RH/SIRH"),
               offre("Product Manager", description="module de paie et gestion des temps")]
        for o in cas:
            self.assertTrue(ro.est_domaine_rh(o), o["intitule"] + " / " + o["description"])

    def test_mot_entier_et_pluriel(self):
        o = offre("Business Analyst", description="Secteur des banques et assurances")
        self.assertEqual(sorted(ro.score_optimum(o, ["banque:3", "assurance:3"])[1]),
                         ["assurance", "banque"])


class TestTitresCibles(unittest.TestCase):
    """Audit du 2026-10-14 : intitulés de poste précis au lieu de « analyst »/« business »."""
    def garde(self, titre):
        gardees, _e, _s = ro.filtrer([offre(titre)], None, ro.EXCLUSIONS_TITRE_PAR_DEFAUT,
                                     [ro.normaliser(t.rpartition(":")[0] or t)
                                      for t in ro.INCLUSIONS_TITRE_PAR_DEFAUT])
        return bool(gardees)

    def test_postes_cibles_gardes(self):
        for t in ("Business Analyst SIRH", "Business Analyts F/H", "Product Owner Data",
                  "Chef de projet MOA F/H", "Analyste fonctionnel Paie", "Business Data Analyst",
                  "System Analyst", "Chef de projet SIRH", "Business Process Owner",
                  "Proxy PO Junior - API et produits digitaux", "Chargé d'Analyse Métiers/ PMO",
                  "Product Manager SaaS B2B",
                  # Gestion de projet SI (oct. 2026)
                  "Chef de projet ERP F/H", "Chef-fe de projet si - plm f/h", "IT Project Manager",
                  "Responsable de Projet ERP Groupe (H/F)", "Business Analyst / Testeur (H/F)"):
            self.assertTrue(self.garde(t), t)

    def test_postes_hors_sujet_ecartes(self):
        for t in ("Finance Business Partner", "Financial Analyst", "Credit Risk Analyst",
                  "Analyste SOC N3", "Business Developer F/H", "Business Unit Manager",
                  "Marketing Analyst",
                  # Audit du 2026-10-14 (2) : homonymes hors SI et stages déguisés
                  "Conducteur de Travaux AMOA - H/F", "Doc Controller AMOA - Bâtiment",
                  "Ingénieur(e) chaine fonctionnelle auto-directeur", "Technicien support fonctionnel N3",
                  "Ingénieur analyse fonctionnelle / automatisme", "Finance Business Analyst - Internship",
                  "PFE – Vers des technosols fonctionnels",
                  # Projet hors SI, test/recette, Scrum Master (non visés, oct. 2026)
                  "Chef de Projet Ferroviaire F/H", "Chef de Projets Construction ENR F/H",
                  "Global Project Manager (m/f/d)", "Testeur fonctionnel F/H", "QA Fonctionnel (H/F)",
                  "Recetteur/Testeur Fonctionnel - CDI", "Scrum Master F/H"):
            self.assertFalse(self.garde(t), t)

    def test_product_manager_exige_un_contexte_numerique(self):
        inclus = [ro.normaliser(t.rpartition(":")[0]) for t in ro.INCLUSIONS_TITRE_PAR_DEFAUT]
        cas = {"Senior Product Manager Emmi Caffè Latte": ("", False),
               "Product Manager Marquage et Dénudage": ("Gamme de machines industrielles", False),
               "Product Manager": ("Vous pilotez la roadmap de notre application", True),
               "Product Owner": ("", True),     # « owner » n'est pas soumis au contexte
               "PMO Senior": ("un portefeuille de projets cybersécurité", True),
               "PMO": ("Suivi de chantier et des sous-traitants", False),
               "Chef de projet ferroviaire": ("Maîtrise des logiciels de planification", False)}
        for titre, (desc, attendu) in cas.items():
            gardees, _e, _s = ro.filtrer([offre(titre, description=desc)], None,
                                         ro.EXCLUSIONS_TITRE_PAR_DEFAUT, inclus)
            self.assertEqual(bool(gardees), attendu, titre)
        # Pré-filtre des sites carrières (titre seul) : « titre » jugé, « texte » laissé passer
        self.assertFalse(ro.titre_retenu("chef de projet ferroviaire", inclus))
        self.assertTrue(ro.titre_retenu("pmo senior", inclus))

    def test_consultant_garde_chez_employeur_direct(self):
        # Cas réel (oct. 2026) : BA Senior Crew Tracking publié « Consultant ... » par Air France
        inclus = [ro.normaliser(t.rpartition(":")[0]) for t in ro.INCLUSIONS_TITRE_PAR_DEFAUT]
        cas = [(offre("Consultant Business Analyst Exigences (H/F)", "SOCIETE AIR FRANCE"), True),
               (offre("Consultant AMOA / Business Analyst (F/H)", "Solutec"), False),
               (offre("Stage - Consultant AMOA SI", "Air France"), False)]   # stage reste exclu
        for o, attendu in cas:
            gardees, _e, _s = ro.filtrer([o], None, ro.EXCLUSIONS_TITRE_PAR_DEFAUT, inclus,
                                         ignorer_esn=True)
            self.assertEqual(bool(gardees), attendu, o["intitule"])

    def test_debut_de_mot(self):
        self.assertFalse(ro.contient_terme("christian dior - chef de projet", "hris"))
        self.assertTrue(ro.contient_terme("business analysts", "business anal"))
        self.assertTrue(ro.contient_terme("stages ete", "stage"))
        self.assertTrue(ro.contient_terme("salesforce proxy po apprentice", "apprenti"))
        self.assertFalse(ro.contient_terme("international project manager", "internship"))
        self.assertFalse(ro.contient_terme("chef de projet amoa", "moa"))   # « amoa » listé à part


class TestClassementESN(unittest.TestCase):
    def test_esn_par_nom(self):
        self.assertEqual(ro.classer(offre(entreprise="Capgemini"))[1], "ESN (nom)")

    def test_signal_texte(self):
        self.assertFalse(ro.classer(offre(description="pour notre client grand compte"))[0])

    def test_grand_groupe_et_assureur_employeurs_directs(self):
        # « nos clients » chez une banque / un assureur = leurs propres clients
        for nom in ("BNP Paribas", "MATMUT", "Thélem assurances", "Air France", "AirFrance"):
            self.assertTrue(ro.classer(offre(entreprise=nom, description="nos clients"))[0], nom)

    def test_cabinet_recrutement_generique(self):
        self.assertTrue(ro.classer(offre(entreprise="AK RECRUTEMENT",
                                         description="pour notre client"))[0])


class TestEsnParOffre(unittest.TestCase):
    def test_une_copie_esn_rend_toutes_les_copies_esn(self):
        # Cas réel : copie France Travail complète (« nos clients ») vs extrait Adzuna
        ft = offre("Product Owner Technique", "Centifolia Conseil",
                   description="Au sein de nos clients grands comptes…") | {"_source": "France Travail"}
        az = offre("Product Owner Technique", "Centifolia Conseil",
                   description="Extrait court") | {"_source": "Adzuna"}
        sig = ro.signatures_esn([ft, az])
        self.assertTrue(ro.est_esn(az, sig))
        gardees, _ex, _st = ro.filtrer([az, ft], None, [], ["owner"])
        self.assertEqual(gardees, [])                     # aucune copie ne passe


class TestGroupes(unittest.TestCase):
    lib, rx = ro.regex_groupes(ro.GRANDS_GROUPES_PAR_DEFAUT)

    def groupe(self, nom):
        return ro.groupe_offre(offre(entreprise=nom), self.lib, self.rx)

    def test_ancre_debut_et_parenthese(self):
        self.assertEqual(self.groupe("VINCI Energies"), "Vinci")
        self.assertEqual(self.groupe("Eurovia (Vinci)"), "Vinci")
        self.assertEqual(self.groupe("Sup de Vinci"), "")          # école, pas le groupe

    def test_filiales(self):
        self.assertEqual(self.groupe("Christian Dior Couture (LVMH)"), "Dior")
        self.assertEqual(self.groupe("LCL (Crédit Agricole)"), "LCL")
        self.assertEqual(self.groupe("Alan Allman Associates"), "")  # ESN, pas Alan SA

    def test_nom_groupe_mot_entier(self):
        self.assertEqual(sc._nom_groupe("AccorHotel", "Accor"), "AccorHotel (Accor)")
        self.assertEqual(sc._nom_groupe("Orange Business", "Orange"), "Orange Business")


class TestLieux(unittest.TestCase):
    def dep(self, lieu):
        return ro.departement_offre(offre(lieu=lieu))

    def test_communes_non_chefs_lieux(self):
        self.assertEqual(self.dep("Joué-lès-Tours"), "37")
        self.assertEqual(self.dep("St-Jean-de-Braye"), "45")
        self.assertEqual(self.dep("Puteaux, Île-de-France, FR"), "92")

    def test_formats_classiques(self):
        self.assertEqual(self.dep("37 - Tours"), "37")
        self.assertEqual(self.dep("Lyon (69003), France"), "69")
        self.assertEqual(self.dep("Toulouse Area, France"), "31")

    def test_noms_de_departement(self):
        self.assertEqual(self.dep("Haute-Garonne, Occitanie"), "31")
        self.assertEqual(self.dep("Indre (36)"), "36")              # pas la commune du 44

    def test_numero_de_rue_pas_un_departement(self):
        lieu = sc._lieu_jobposting({"jobLocation": [{"address": {
            "addressLocality": "1973 Bd de la Défense, 92000 Nanterre, France"}}]})
        self.assertEqual(lieu, "Nanterre (92000), France")
        self.assertEqual(self.dep(lieu), "92")

    def test_lieux_workday_france(self):
        for lib in ("Lyon, France", "France - Paris", "FRA-EPE"):
            self.assertTrue(sc._RE_LIEU_FRANCE.search(lib), lib)
        for lib in ("Frankfurt", "San Francisco, CA", "Franklin, TN"):
            self.assertFalse(sc._RE_LIEU_FRANCE.search(lib), lib)


class TestGeo(unittest.TestCase):
    def test_coords_et_distances(self):
        dom = ro.coords_lieu("Lamotte-Beuvron")
        self.assertIsNotNone(dom)
        self.assertEqual(ro.coords_lieu("41600"), dom)                 # code postal
        self.assertAlmostEqual(ro.distance_km(dom, ro.coords_lieu("Orléans")), 31, delta=3)
        self.assertAlmostEqual(ro.distance_km(dom, ro.coords_lieu("Tours")), 103, delta=4)

    def test_geoloc_offre(self):
        # coordonnees de la source prioritaires, sinon libelle (FT « 41 - Ville »)
        self.assertEqual(ro.geoloc_offre(offre(lieu="x") | {"lieuTravail": {
            "libelle": "x", "latitude": 47.0, "longitude": 2.0}}), (47.0, 2.0))
        self.assertEqual(ro.geoloc_offre(offre(lieu="41 - Lamotte-Beuvron")),
                         ro.coords_lieu("Lamotte-Beuvron"))
        self.assertIsNone(ro.geoloc_offre(offre(lieu="Île-de-France")))  # region seule
        self.assertIsNone(ro.coords_lieu("Indre"))                       # nom de departement

    def test_geoloc_approx_prefecture(self):
        # Cas réels (oct. 2026) : FT « 95 - Val-d'Oise » (Air France), « Île-de-France » seul
        self.assertIsNone(ro.geoloc_offre(offre(lieu="95 - Val-d'Oise")))
        self.assertEqual(ro.geoloc_approx(offre(lieu="95 - Val-d'Oise")), ro.coords_lieu("Cergy"))
        self.assertEqual(ro.geoloc_approx(offre(lieu="Île-de-France")), ro.coords_lieu("Paris"))
        self.assertIsNone(ro.geoloc_approx(offre(lieu="France")))

    def test_homonyme_hors_region_citee(self):
        # Vinci « Saint-Denis, Ile-de-France » : pas Saint-Denis de La Réunion (plus peuplée)
        o = offre(lieu="Saint-Denis, Ile-de-France, France")
        self.assertNotEqual(ro.departement_offre(o), "974")
        self.assertIsNone(ro.geoloc_offre(o))
        self.assertEqual(ro.geoloc_approx(o), ro.coords_lieu("Paris"))
        self.assertEqual(ro.departement_offre(offre(lieu="Le Mans, Pays de la Loire")), "72")


class TestRangCarte(unittest.TestCase):
    def rang(self, rh=True, score=10, loc=False, esn=False, gg=False):
        return ro.rang_carte(rh, score, loc, esn, gg, seuil=8)

    def test_hierarchie(self):
        self.assertEqual(self.rang(loc=True, gg=True), 1)
        self.assertEqual(self.rang(gg=True), 2)
        self.assertEqual(self.rang(), 3)                       # RH + élevé + sans ESN
        self.assertEqual(self.rang(loc=True, esn=True), 4)     # ESN localisée
        self.assertEqual(self.rang(esn=True), 5)
        self.assertEqual(self.rang(rh=False), 6)               # score élevé seul
        self.assertEqual(self.rang(score=5), 7)                # RH mais score < seuil
        self.assertEqual(self.rang(esn=True, gg=True, loc=True), 4)  # ESN jamais 1-3

    def test_seuil(self):
        self.assertEqual(ro.rang_carte(False, 8, False, False, False, seuil=8), 6)
        self.assertEqual(ro.rang_carte(False, 7, False, False, False, seuil=8), 7)


class TestSuisse(unittest.TestCase):
    def test_localites_multilingues_et_formats(self):
        geneve = ro.coords_suisse("Genève")
        self.assertIsNotNone(geneve)
        self.assertEqual(ro.coords_suisse("Geneva, Lausanne"), geneve)
        self.assertIsNotNone(ro.coords_suisse("EMEA &gt; CHE &gt; Stabio"))
        self.assertIsNotNone(ro.coords_suisse("Grand-Lancy"))         # -> Lancy
        self.assertIsNotNone(ro.coords_suisse("Lausanne VD"))         # code canton

    def test_offre_suisse_pas_dans_la_table_francaise(self):
        o = offre(lieu="Basel") | {"_source": "jobup.ch"}
        self.assertEqual(ro.geoloc_offre(o), ro.coords_suisse("Basel"))


class TestDates(unittest.TestCase):
    def test_formats_libres(self):
        self.assertEqual(sc._date_libre("Oct 8, 2026"), "2026-10-08")
        self.assertEqual(sc._date_libre("Thu Oct 01 02:00:00 UTC 2026"), "2026-10-01")
        self.assertEqual(sc._date_libre("8 oct. 2026"), "2026-10-08")
        self.assertEqual(sc._date_libre("13 juil. 2026"), "2026-07-13")
        self.assertEqual(sc._date_libre("pas une date"), "")

    def test_workday_age(self):
        self.assertEqual(sc._workday_age_jours("Posted Today"), 0)
        self.assertEqual(sc._workday_age_jours("Posted 30+ Days Ago"), 30)

    def test_fuseau(self):
        self.assertEqual(sc._iso("2026-09-22T10:24:00+0000"), "2026-09-22T10:24:00+00:00")


class TestFiltrer(unittest.TestCase):
    def test_inclusion_exclusion_doublons(self):
        brutes = [offre("Business Analyst SIRH"), offre("Business Analyst SIRH"),
                  offre("Comptable"), offre("Data Analyst")]
        gardees, _exclues, stats = ro.filtrer(brutes, None, ro.EXCLUSIONS_TITRE_PAR_DEFAUT,
                                              ["analyst", "business"])
        self.assertEqual([o["intitule"] for o in gardees], ["Business Analyst SIRH"])
        self.assertEqual(stats["doublons"], 1)

    def test_doublons_suffixes_et_raison_sociale(self):
        # Copies réelles (oct. 2026) : « (CDI) » ajouté par l'agrégateur, raison sociale variable
        for a, b in ((offre("Product Owner CRM (F/H)", "MACIF"), offre("Product owner crm f/h (CDI)", "Macif")),
                     (offre("Product Owner WMS (H/F)", "ARES"), offre("Product owner wms - f/h", "Groupe Ares")),
                     (offre("Infrastructure Product Owner (F/H)", "Thales"),
                      offre("Infrastructure Product Owner (F/H) (CDI)", "THALES SA"))):
            self.assertEqual(ro.signature(a), ro.signature(b))
        self.assertNotEqual(ro.signature(offre("Product Owner", "Crédit Agricole")),
                            ro.signature(offre("Product Owner", "Crédit Mutuel")))


class TestCreneauEmail(unittest.TestCase):
    def soiree(self, texte):
        from datetime import datetime
        return ds.soiree_envoi(datetime.fromisoformat(texte))

    def test_creneau_17h_06h(self):
        self.assertIsNone(self.soiree("2026-10-14 16:59"))          # trop tôt
        self.assertEqual(self.soiree("2026-10-14 17:00"), "2026-10-14")
        self.assertEqual(self.soiree("2026-10-14 23:30"), "2026-10-14")
        self.assertEqual(self.soiree("2026-10-15 01:30"), "2026-10-14")  # compte pour la veille
        self.assertEqual(self.soiree("2026-10-15 05:59"), "2026-10-14")
        self.assertIsNone(self.soiree("2026-10-15 06:00"))          # matin : hors créneau


class TestSourcesMuettes(unittest.TestCase):
    def test_chute_apres_volume_normal(self):
        h = [{"A": 40, "S": 2}] * 3 + [{"A": 0, "S": 0}] * 3
        self.assertEqual(rs.sources_muettes(h), ["A"])   # S oscille à 2 : pas d'alerte

    def test_historique_trop_court(self):
        self.assertEqual(rs.sources_muettes([{"A": 0}] * 3), [])


if __name__ == "__main__":
    unittest.main()
