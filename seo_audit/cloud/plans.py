"""Plans d'abonnement et quotas. Les prix sont indicatifs : le paiement (Stripe) viendra plus tard ;
en attendant, le plan d'un utilisateur se change dans la table profiles (colonne plan)."""

PLANS = {
    "gratuit": {"label": "Gratuit", "price": 0, "max_pages": 100, "audits_month": 5, "running": 1,
                "competitors_month": 3, "competitor_pages": 20, "keywords_month": 10, "gsc_properties": 1,
                "tracked_keywords": 10},
    "pro": {"label": "Pro", "price": 19, "max_pages": 2000, "audits_month": 60, "running": 3,
            "competitors_month": 30, "competitor_pages": 50, "keywords_month": 200, "gsc_properties": 10,
            "tracked_keywords": 200},
    "agence": {"label": "Agence", "price": 49, "max_pages": 10000, "audits_month": 300, "running": 10,
               "competitors_month": 150, "competitor_pages": 100, "keywords_month": 1000, "gsc_properties": 50,
               "tracked_keywords": 1000},
}
# type d'usage → (clé du quota mensuel, libellé)
USAGE = {
    "audit": ("audits_month", "audits ce mois-ci"),
    "competitor": ("competitors_month", "analyses de concurrents ce mois-ci"),
    "keywords": ("keywords_month", "recherches de mots-clés ce mois-ci"),
}


class QuotaError(Exception):
    pass


def limits(plan: str) -> dict:
    return PLANS.get(plan, PLANS["gratuit"])


def check_monthly(plan: str, kind: str, used: int) -> None:
    key, label = USAGE[kind]
    limit = limits(plan)[key]
    if used >= limit:
        raise QuotaError(f"Limite atteinte : {limit} {label} avec le plan {limits(plan)['label']}.")
