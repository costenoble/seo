"""Moteur d'audit reprenable : le crawl avance par étapes courtes (adapté aux fonctions sans état de Vercel).

L'état complet d'un audit vit dans un JobStore (Postgres en production, mémoire en test) ;
n'importe quelle instance peut exécuter l'étape suivante."""
from .steps import run_tick, step_audit

__all__ = ["run_tick", "step_audit"]
