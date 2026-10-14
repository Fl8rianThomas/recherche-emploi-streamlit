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
