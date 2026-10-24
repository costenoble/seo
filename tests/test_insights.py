from seo_audit.cloud.insights import analyze, brand_of

Q = lambda q, imp, pos, clicks=0: {"query": q, "impressions": imp, "position": pos, "clicks": clicks}
QP = lambda q, page, imp, pos, clicks=0: {"query": q, "page": page, "impressions": imp, "position": pos, "clicks": clicks}
P = lambda page, imp, pos, clicks=0: {"page": page, "impressions": imp, "position": pos, "clicks": clicks}
SITE = "https://kompta.pro"


def kompta():
    queries = [Q("kompta", 339, 3.8, 25), Q("compta pro", 30, 40), Q("coppta", 12, 30), Q("b2brouter", 31, 13.1, 1),
               Q("btobrouter", 39, 9.1), Q("b2brouter avis", 20, 7), Q("logiciel facture charpentier", 249, 63),
               Q("facture plaquiste", 25, 19), Q("factur-x", 40, 8, 1)]
    qp = [QP("b2brouter", SITE + "/blog/b2brouter", 31, 13.1, 1), QP("btobrouter", SITE + "/blog/b2brouter", 39, 9.1),
          QP("b2brouter avis", SITE + "/blog/b2brouter", 20, 7), QP("factur-x", SITE + "/guide/factur-x", 40, 8, 1),
          QP("factur-x", SITE + "/blog/factur-x-2026", 30, 12),
          QP("logiciel facture charpentier", SITE + "/logiciel-facturation-charpentier", 249, 63)]
    pages = [P(SITE + "/", 400, 4, 26), P("http://kompta.pro/", 15, 6), P(SITE + "/login?tab=register", 12, 9),
             P(SITE + "/blog/b2brouter", 90, 9, 1), P(SITE + "/logiciel-facturation-charpentier", 249, 63),
             P(SITE + "/logiciel-facturation-couvreur", 143, 84), P(SITE + "/logiciel-facturation-menuisier", 99, 80)]
    audit = {"issues": [{"url": SITE + "/blog/b2brouter", "code": "title_too_long"}],
             "pages": [{"url": SITE + "/", "title": "Kompta — facturation pour artisans"}]}
    return queries, qp, pages, audit


def test_brand_name():
    assert brand_of("sc-domain:kompta.pro") == "kompta"
    assert brand_of("https://www.vigie-seo.fr/") == "vigieseo"


def test_kompta_case():
    queries, qp, pages, audit = kompta()
    r = analyze("sc-domain:kompta.pro", queries, qp, pages, audit,
                recent=[{"query": "factur-x", "position": 12, "previous_position": 7, "impressions": 40}])
    by = {i["id"]: i for i in r["insights"]}
    assert r["insights"][0]["priority"] == "high"
    assert "3,8e" in by["brand"]["text"] and "coppta" in by["brand"]["chips"] and "compta pro" in by["brand"]["chips"]
    assert "déjà en tête" in by["brand"]["action"]
    assert "http et en https" in by["technical"]["text"] and "/login?tab=register" in by["technical"]["text"]
    wins = by["quick_wins"]["table"]["rows"]
    assert wins[0][0] == "/blog/b2brouter" and "title trop long" in wins[0][-1]
    assert by["page_two"]["table"]["rows"][0][0] == "facture plaquiste"
    assert "logiciel-facturation-…" in by["potential"]["text"]
    assert by["potential"]["table"]["rows"][0][0] == "/logiciel-facturation-charpentier"
    assert by["cannibalization"]["table"]["rows"][0][0] == "factur-x"
    assert by["drops"]["table"]["rows"][0][0] == "factur-x"


def test_good_brand_and_small_sites():
    r = analyze("sc-domain:kompta.pro", [Q("kompta", 300, 1.1, 120), Q("autre", 50, 20)], [], [])
    brand = next(i for i in r["insights"] if i["id"] == "brand")
    assert brand["priority"] == "good" and r["insights"][-1] is brand  # les bons points viennent en dernier
    assert "relancez l'import" in r["note"]
    tiny = analyze("https://cstlab.dev/", [Q("cstlab", 33, 4.2, 4)], [], [])
    assert tiny["insights"] == [] and "assez de données" in tiny["note"]
