#!/bin/bash
# Lança a instância de processamento sob demanda e acompanha até o fim (doc 03 §7.8, ADR-14).
# Uso: scripts/run-job.sh aggregates --start AAAA-MM-DD --end AAAA-MM-DD [--allow-holdout]
#                         [--version <versão publicada>] [--max-hours <1-9>]
# Perfil AWS: $AWS_PROFILE (padrão "trader"). Mostra a estimativa de custo e pede confirmação.
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_PROFILE="${AWS_PROFILE:-trader}"
export AWS_REGION="us-east-2"
ACCOUNT="019419901344"
PRICE_HOUR="0.294" # c7g.2xlarge US$ 0,289/h + IPv4 público US$ 0,005/h (doc 03 §7.8)

usage() {
  sed -n '3,5p' "$0" >&2
  exit 2
}
[ "${1:-}" = "aggregates" ] || usage
shift
START="" END="" HOLDOUT="" VERSION="" MAX_HOURS=3
while [ $# -gt 0 ]; do
  case "$1" in
    --start) START="${2:-}"; shift 2 ;;
    --end) END="${2:-}"; shift 2 ;;
    --allow-holdout) HOLDOUT="--allow-holdout"; shift ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --max-hours) MAX_HOURS="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
DATE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
[[ "$START" =~ $DATE_RE && "$END" =~ $DATE_RE ]] || usage
[[ "$MAX_HOURS" =~ ^[1-9]$ ]] || { echo "--max-hours deve ser de 1 a 9" >&2; exit 2; }

# O holdout é recusado aqui, antes de qualquer custo, e de novo na instância (RF-179).
uv run --quiet python - "$START" "$END" "$HOLDOUT" <<'EOF'
import sys
from datetime import date

from trader.core.holdout import HOLDOUT_A, holdout_days

start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
if start > end:
    sys.exit(f"início {start} depois do fim {end}")
reserved = holdout_days(start, end)
if reserved and sys.argv[3] != "--allow-holdout":
    sys.exit(
        f"recusado: {len(reserved)} dia(s) do holdout da trilha A ({HOLDOUT_A[0]} a"
        f" {HOLDOUT_A[1]}); só com --allow-holdout, na avaliação final (doc 04 §3.3)"
    )
EOF

if [ "$(aws sts get-caller-identity --query Account --output text)" != "$ACCOUNT" ]; then
  echo "o perfil $AWS_PROFILE não é da conta $ACCOUNT; nada foi lançado" >&2
  exit 1
fi

tf_output() { terraform -chdir=infra/envs/prod output -raw "$1"; }
BUCKET="$(tf_output data_bucket)"
TEMPLATE="$(tf_output job_launch_template)"
LOG_GROUP="$(tf_output job_log_group)"
UV_VERSION="$(tf_output uv_version)"
VERSION="${VERSION:-$(uv version --short)-$(git rev-parse --short HEAD)}"
if ! aws s3 ls "s3://$BUCKET/artifacts/$VERSION/SHA256SUMS" >/dev/null; then
  echo "versão $VERSION não publicada; rode scripts/publish-artifact.sh" >&2
  exit 1
fi

MAX_COST="$(python3 -c "print(f'{$PRICE_HOUR * $MAX_HOURS:.2f}')")"
echo "Job: aggregates de $START a $END ${HOLDOUT:+(com holdout) }com a versão $VERSION"
echo "Custo: c7g.2xlarge sob demanda, ~US\$ $PRICE_HOUR/h, pago pelos créditos; ~2 h para 9"
echo "meses (~US\$ 0,60). A instância se encerra sozinha em no máximo $MAX_HOURS h (~US\$ $MAX_COST)."
read -r -p "Lançar? [s/N] " ANSWER
[ "$ANSWER" = "s" ] || { echo "nada foi lançado"; exit 1; }

JOB_ID="aggregates-$(date -u +%Y%m%dT%H%M%SZ)"
ARGS="aggregates --start $START --end $END${HOLDOUT:+ $HOLDOUT}"
USER_DATA="$(mktemp)"
trap 'rm -f "$USER_DATA"' EXIT
sed -e "s|@@JOB_ID@@|$JOB_ID|g" -e "s|@@VERSION@@|$VERSION|g" -e "s|@@BUCKET@@|$BUCKET|g" \
  -e "s|@@REGION@@|$AWS_REGION|g" -e "s|@@LOG_GROUP@@|$LOG_GROUP|g" \
  -e "s|@@UV_VERSION@@|$UV_VERSION|g" -e "s|@@MAX_MINUTES@@|$((MAX_HOURS * 60))|g" \
  -e "s|@@ARGS@@|$ARGS|g" scripts/job-bootstrap.sh >"$USER_DATA"
if grep -q '@@' "$USER_DATA"; then
  echo "campo sem valor no script de inicialização" >&2
  exit 1
fi

INSTANCE="$(aws ec2 run-instances --launch-template "LaunchTemplateName=$TEMPLATE" \
  --user-data "file://$USER_DATA" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=trader-job},{Key=JobId,Value=$JOB_ID}]" \
  --query 'Instances[0].InstanceId' --output text)"
LAUNCHED="$(date +%s)"
echo "instância $INSTANCE lançada para o job $JOB_ID; acompanhando (Ctrl+C só para de acompanhar)"

TAIL_PID=""
while true; do
  STATE="$(aws ec2 describe-instances --instance-ids "$INSTANCE" \
    --query 'Reservations[0].Instances[0].State.Name' --output text)"
  if [ -z "$TAIL_PID" ] && aws logs describe-log-streams --log-group-name "$LOG_GROUP" \
      --log-stream-name-prefix "$INSTANCE" --query 'logStreams[0].logStreamName' \
      --output text 2>/dev/null | grep -q "$INSTANCE"; then
    aws logs tail "$LOG_GROUP" --log-stream-names "$INSTANCE" --follow --format short &
    TAIL_PID=$!
  fi
  case "$STATE" in terminated | shutting-down) break ;; esac
  sleep 30
done
[ -n "$TAIL_PID" ] && sleep 10 && kill "$TAIL_PID" 2>/dev/null || true
MINUTES=$((($(date +%s) - LAUNCHED + 59) / 60))
echo "instância encerrada depois de ~$MINUTES min (~US\$ $(python3 -c "print(f'{$PRICE_HOUR * $MINUTES / 60:.2f}')"))"

OUT="data/jobs/$JOB_ID"
mkdir -p "$OUT"
if ! aws s3 cp --only-show-errors "s3://$BUCKET/derived/ops/jobs/$JOB_ID/manifest.json" "$OUT/"; then
  echo "sem manifesto; o log está em s3://$BUCKET/derived/ops/jobs/$JOB_ID/job.log" >&2
  exit 1
fi
aws s3 cp --only-show-errors "s3://$BUCKET/derived/ops/jobs/$JOB_ID/job.log" "$OUT/" || true
python3 - "$OUT/manifest.json" <<'EOF'
import json
import sys

m = json.load(open(sys.argv[1]))
units = m.get("units", [])
print(f"status: {m['status']}" + (f" ({m['error']})" if m.get("error") else ""))
print(f"duração: {m.get('duration_s')} s · versão: {m['code_version']}")
print(
    f"dias de arquivo: {sum(u['kind'] == 'archive' for u in units)}"
    f" · horas ao vivo: {sum(u['kind'] == 'live' for u in units)}"
    f" · negócios: {m.get('trades', 0)} · saídas: {len(m.get('outputs', []))}"
)
print(f"pico de memória por processo: {m.get('max_rss_mb')} MB")
for warning in m.get("warnings", []):
    print(f"aviso: {warning}")
sys.exit(0 if m["status"] == "ok" else 1)
EOF
echo "manifesto e log em $OUT/"
