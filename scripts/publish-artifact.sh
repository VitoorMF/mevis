#!/bin/bash
# Publica a versão atual do código em s3://<bucket>/artifacts/<versão>/ (doc 03 §9.1).
# Uso: scripts/publish-artifact.sh   (perfil AWS: $AWS_PROFILE, padrão "trader")
# Exige árvore Git limpa: a versão publicada é sempre um commit.
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${AWS_PROFILE:-trader}"
if [ -n "$(git status --porcelain)" ]; then
  echo "há alterações não commitadas; faça o commit antes de publicar" >&2
  exit 1
fi

LABEL="$(uv version --short)-$(git rev-parse --short HEAD)"
OUT="dist/artifacts/$LABEL"
rm -rf "$OUT"
mkdir -p "$OUT"
uv build --wheel --out-dir "$OUT"
# Sem grupos: o `pesquisa` (numpy) fica fora do host até a V0.3 (RNF-029).
uv export --no-default-groups --no-emit-project --format requirements-txt --output-file "$OUT/requirements.txt" >/dev/null
(cd "$OUT" && shasum -a 256 ./*.whl requirements.txt > SHA256SUMS)

BUCKET="$(terraform -chdir=infra/envs/prod output -raw data_bucket)"
aws s3 cp --profile "$PROFILE" --recursive "$OUT" "s3://$BUCKET/artifacts/$LABEL/"
echo "$LABEL"
