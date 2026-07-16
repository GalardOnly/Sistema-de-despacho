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
        "alembic.ini": ROOT / "alembic.ini",
        "ambiente Alembic": ROOT / "migrations" / "env.py",
        "migration inicial": ROOT / "migrations" / "versions" / "001_initial.py",
        "migration operadores normalizados": ROOT / "migrations" / "versions" / "005_operadores_normalizados.py",
        "migration urgência mista": ROOT / "migrations" / "versions" / "002_urgencia_mista.py",
        "migration despacho atômico": ROOT / "migrations" / "versions" / "003_despacho_atomico.py",
        "migration revogação de sessão": ROOT / "migrations" / "versions" / "004_revogacao_sessao.py",
        "inicializador SQLite": ROOT / "scripts" / "init_sqlite.py",
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
        exigir("alembic" in requirements.casefold(), "Alembic ausente", erros)
        exigir("redis" in requirements.casefold(), "Cliente Redis ausente", erros)
        exigir("gunicorn" in requirements.casefold(), "Gunicorn não está nas dependências", erros)
        exigir("USER app" in dockerfile, "Contêiner deve executar sem usuário root", erros)
        exigir(
            "COPY migrations ./migrations" in dockerfile,
            "Migrations Alembic ausentes da imagem",
            erros,
        )

    if erros:
        for erro in erros:
            print(f"ERRO: {erro}")
        return 1

    print("Preflight Google Cloud concluído: configuração PostgreSQL de homologação válida.")
    print("Confirme a migration do Supabase e o secret despacho-database-url antes do deploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
