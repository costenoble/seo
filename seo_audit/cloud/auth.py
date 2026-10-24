"""Vérification des jetons de session Supabase envoyés par le navigateur (en-tête Authorization: Bearer)."""
from functools import lru_cache

import jwt


class AuthError(Exception):
    pass


@lru_cache(maxsize=4)
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_token(token: str, settings) -> dict:
    """Renvoie {id, email} ; lève AuthError si le jeton est absent, expiré, falsifié ou d'un autre projet."""
    if not token:
        raise AuthError("Connexion requise")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise AuthError("Jeton HS256 refusé : SUPABASE_JWT_SECRET non configuré")
            key = settings.supabase_jwt_secret
        elif alg in ("ES256", "RS256"):
            key = _jwks(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token).key
        else:
            raise AuthError("Algorithme de jeton non pris en charge")
        claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated",
                            issuer=f"{settings.supabase_url}/auth/v1", options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise AuthError(f"Session invalide : {exc}") from exc
    meta = claims.get("user_metadata") or {}
    return {"id": claims["sub"], "email": claims.get("email"), "name": (meta.get("full_name") or meta.get("name") or "").strip() or None}
