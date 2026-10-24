"""Point d'entrée Vercel : toutes les routes /api/* sont réécrites vers cette fonction (voir vercel.json)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seo_audit.cloud.app import create_app  # noqa: E402

app = create_app()
