"""Chiffrement symétrique (Fernet) des jetons Google stockés en base.
Générer une clé : python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" """
from cryptography.fernet import Fernet, InvalidToken


def encrypt(key: str, value: str) -> str:
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt(key: str, value: str) -> str | None:
    try:
        return Fernet(key.encode()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None
