"""Valida os arquivos mínimos antes de enviar a homologação ao Google Cloud."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def exigir(condicao, mensagem, erros):
    if not condicao:
        erros.append(mensagem)


def main():
    erros = []
    arquivos = {
        "Dockerfile": ROOT / "Dockerfile",
        "cloudbuild.yaml": ROOT / "cloudbuild.yaml",
        "gunicorn.conf.py": ROOT / "gunicorn.conf.py",
        "requirements.txt": ROOT / "requirements.txt",
        ".dockerignore": ROOT / ".dockerignore",
        ".gcloudignore": ROOT / ".gcloudignore",
        "migration PostgreSQL": ROOT / "database" / "migrations" / "postgresql" / "001_initial.sql",
        "migration urgência mista": ROOT / "database" / "migrations" / "postgresql" / "002_urgencia_mista.sql",
        "migration despacho atômico": ROOT / "database" / "migrations" / "postgresql" / "003_despacho_atomico.sql",
        "inicializador Supabase": ROOT / "scripts" / "init_supabase.py",
    }
    for nome, caminho in arquivos.items():
        exigir(caminho.is_file(), f"Arquivo ausente: {nome}", erros)

    if not erros:
        cloudbuild = arquivos["cloudbuild.yaml"].read_text(encoding="utf-8")
        requirements = arquivos["requirements.txt"].read_text(encoding="utf-8")
        dockerfile = arquivos["Dockerfile"].read_text(encoding="utf-8")
        exigir("APP_ENV=homologation" in cloudbuild, "Cloud Build não está em homologação", erros)
        exigir("DATABASE_URL=despacho-database-url:latest" in cloudbuild, "Secret do PostgreSQL ausente", erros)
        exigir("ALLOW_EPHEMERAL_SQLITE" not in cloudbuild, "Cloud Build ainda permite SQLite efêmero", erros)
        exigir("psycopg" in requirements.casefold(), "Driver PostgreSQL ausente", erros)
        exigir("sqlalchemy" in requirements.casefold(), "Camada de banco PostgreSQL ausente", erros)
        exigir("gunicorn" in requirements.casefold(), "Gunicorn não está nas dependências", erros)
        exigir("USER app" in dockerfile, "Contêiner deve executar sem usuário root", erros)

    if erros:
        for erro in erros:
            print(f"ERRO: {erro}")
        return 1

    print("Preflight Google Cloud concluído: configuração PostgreSQL de homologação válida.")
    print("Confirme a migration do Supabase e o secret despacho-database-url antes do deploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
