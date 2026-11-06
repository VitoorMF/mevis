#!/bin/bash
# Script de inicialização da instância de processamento sob demanda (doc 03 §7.8, ADR-14).
# Modelo: scripts/run-job.sh preenche os campos entre arrobas duplas e envia como user_data.
# Roda uma vez, no boot, e termina sempre com `shutdown`: o comportamento `terminate` apaga a
# instância e o disco.
shutdown -h +@@MAX_MINUTES@@ "trava do processamento sob demanda"
set -uo pipefail

JOB_ID="@@JOB_ID@@"
VERSION="@@VERSION@@"
BUCKET="@@BUCKET@@"
REGION="@@REGION@@"
LOG_GROUP="@@LOG_GROUP@@"
UV_VERSION="@@UV_VERSION@@"
LOG=/var/log/trader-job.log
WORK=/var/tmp/trader-job
JOB_PREFIX="s3://$BUCKET/derived/ops/jobs/$JOB_ID"

touch "$LOG"
chmod 0644 "$LOG"
exec > >(tee -a "$LOG") 2>&1

finish() {
  local code=$?
  echo "fim do job $JOB_ID: código $code, $(date -u +%FT%TZ)"
  sleep 2  # o tee termina de gravar o log
  aws s3 cp --region "$REGION" --only-show-errors "$LOG" "$JOB_PREFIX/job.log" || true
  /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a stop || true
  shutdown -h now
}
trap finish EXIT
set -e

echo "job $JOB_ID: versão $VERSION, início $(date -u +%FT%TZ)"

# Log no CloudWatch (/trader/jobs), para acompanhar do MacBook.
dnf install -y -q amazon-cloudwatch-agent python3-pip
cat > /opt/aws/amazon-cloudwatch-agent/etc/job.json <<EOF
{
  "agent": { "run_as_user": "cwagent" },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          { "file_path": "$LOG", "log_group_name": "$LOG_GROUP", "log_stream_name": "{instance_id}" }
        ]
      }
    }
  }
}
EOF
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/etc/job.json

# Mesma instalação do trader-deploy do host (doc 03 §9.1): artefato conferido pelo SHA-256.
pip3 install -q "uv==$UV_VERSION"
export UV_PYTHON_INSTALL_DIR=/opt/trader/python UV_CACHE_DIR=/var/cache/trader-uv
ARTIFACT="$(mktemp -d)"
aws s3 cp --region "$REGION" --recursive --only-show-errors \
  "s3://$BUCKET/artifacts/$VERSION/" "$ARTIFACT/"
(cd "$ARTIFACT" && sha256sum -c SHA256SUMS)
uv venv -q --python 3.12 /opt/trader/job
uv pip install -q --python /opt/trader/job/bin/python --require-hashes -r "$ARTIFACT/requirements.txt"
uv pip install -q --python /opt/trader/job/bin/python --no-deps "$ARTIFACT"/*.whl

install -d "$WORK" /etc/trader
cat > /etc/trader/job.toml <<EOF
[pipeline]
data_dir = "$WORK"
replica_s3_bucket = "$BUCKET"
aws_region = "$REGION"
EOF

/opt/trader/job/bin/trader @@ARGS@@ --config /etc/trader/job.toml \
  --workers "$(nproc)" --job-id "$JOB_ID" --code-version "$VERSION"
