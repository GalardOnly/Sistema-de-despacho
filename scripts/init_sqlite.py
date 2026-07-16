import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from corridas.despacho import DESP_DB_PATH, init_db_desp


def main():
    init_db_desp()
    print(f"Banco SQLite preparado: {DESP_DB_PATH}")


if __name__ == "__main__":
    main()
