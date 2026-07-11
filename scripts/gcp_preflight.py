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
    }
    for nome, caminho in arquivos.items():
        exigir(caminho.is_file(), f"Arquivo ausente: {nome}", erros)

    if not erros:
        cloudbuild = arquivos["cloudbuild.yaml"].read_text(encoding="utf-8")
        requirements = arquivos["requirements.txt"].read_text(encoding="utf-8")
        dockerfile = arquivos["Dockerfile"].read_text(encoding="utf-8")
        exigir("APP_ENV=homologation" in cloudbuild, "Cloud Build não está em homologação", erros)
        exigir("ALLOW_EPHEMERAL_SQLITE=1" in cloudbuild, "Opt-in do SQLite efêmero ausente", erros)
        exigir("--max=1" in cloudbuild, "Homologação SQLite deve usar uma instância", erros)
        exigir("gunicorn" in requirements.casefold(), "Gunicorn não está nas dependências", erros)
        exigir("USER app" in dockerfile, "Contêiner deve executar sem usuário root", erros)

    if erros:
        for erro in erros:
            print(f"ERRO: {erro}")
        return 1

    print("Preflight Google Cloud concluído: configuração de homologação válida.")
    print("Aviso: dados do SQLite em /tmp são efêmeros e devem ser fictícios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
