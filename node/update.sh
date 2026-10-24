#!/bin/sh
# update.sh <env file> <desired url>
#
# Pull-based update of a node. It downloads node/desired.env from the repository (the images, by digest), checks the
# cosign signature of every tantive image that changed, and only then rewrites the images in <env file> and runs
# `docker compose up -d`. An image whose signature does not verify is never started.
#
# Exit codes: 0 up to date or updated; 1 refused or failed to apply (the env file is left as it was);
#             2 the desired file could not be downloaded.
#
# Trust: the tantive images (VERIFIED_KEYS) are verified with cosign, keyless, against the identity of the `build`
# workflow of github.com/scombat/s1d1-tantive (the invocation of s1d1-tantive/docs/verify.md). cloudflared is not
# signed by us: it is only checked to be pinned by digest in its own repository (docker.io/cloudflare/cloudflared), and
# it changes only through a digest change in node/desired.env, which goes through review of a pull request here.
# desired.env may set these two keys only, each bound to its repository; anything else refuses the whole file.
#
# The secrets (TUNNEL_TOKEN, COMMUNITY_TOKEN) live in <env file> only. They are never read, printed or logged here.
set -eu
umask 077

VERIFIED_KEYS="TANTIVE_IMAGE"
IDENTITY_REGEXP='^https://github.com/scombat/s1d1-tantive/\.github/workflows/build\.yml@refs/tags/v'
OIDC_ISSUER='https://token.actions.githubusercontent.com'

if [ $# -ne 2 ]; then
  echo "usage: update.sh <env file> <desired url>" >&2
  exit 1
fi
ENV_FILE=$1
DESIRED_URL=$2
NODE_DIR=$(dirname "$ENV_FILE")
COMPOSE_FILE=${S1D1_COMPOSE_FILE:-$NODE_DIR/compose.yaml}

WORK=$(mktemp -d "$NODE_DIR/.update.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM
DESIRED=$WORK/desired.env
CHANGES=$WORK/changes.env
: >"$CHANGES"

# ---- 1. download the desired images ------------------------------------------------------------------------------
if ! curl -fsSL --proto '=https' --tlsv1.2 --retry 2 --max-time 30 -o "$DESIRED" "$DESIRED_URL"; then
  echo "update: cannot download $DESIRED_URL" >&2
  exit 2
fi

# ---- 2. parse it: only NAME_IMAGE=repo[:tag]@sha256:<64 hex> lines, comments and blank lines ---------------------
current_value() { # current_value <KEY>: the value in the env file, empty when absent
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

while IFS= read -r line || [ -n "$line" ]; do
  case $line in
    '' | '#'*) continue ;;
  esac
  key=${line%%=*}
  value=${line#*=}
  # a closed list of keys, each bound to its own repository: no other registry or repository can be started
  case $key in
    TANTIVE_IMAGE) pattern='^ghcr\.io/scombat/s1d1-tantive:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$' ;;
    CLOUDFLARED_IMAGE) pattern='^docker\.io/cloudflare/cloudflared:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$' ;;
    *)
      echo "update: refused, $key is not a key desired.env may set (TANTIVE_IMAGE, CLOUDFLARED_IMAGE)" >&2
      exit 1
      ;;
  esac
  if ! printf '%s\n' "$value" | grep -Eq "$pattern"; then
    echo "update: refused, $key is not <its repository>:<tag>@sha256:<64 hex> (explicit registry, pinned by digest)" >&2
    exit 1
  fi
  if [ "$value" != "$(current_value "$key")" ]; then
    printf '%s=%s\n' "$key" "$value" >>"$CHANGES"
  fi
done <"$DESIRED"

if [ ! -s "$CHANGES" ]; then
  echo "update: up to date"
  exit 0
fi

# ---- 3. verify every changed tantive image before touching anything ----------------------------------------------
while IFS= read -r change; do
  key=${change%%=*}
  image=${change#*=}
  case " $VERIFIED_KEYS " in
    *" $key "*)
      echo "update: verifying $image"
      if ! cosign verify "$image" \
        --certificate-identity-regexp "$IDENTITY_REGEXP" \
        --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null; then
        echo "update: refused, the signature of $image does not verify" >&2
        exit 1
      fi
      ;;
    *) echo "update: $key pinned by digest ($image), not cosign-verified" ;;
  esac
done <"$CHANGES"

# ---- 4. write the env atomically, then start ---------------------------------------------------------------------
# $WORK is a subfolder of the target folder (same filesystem), and the trap removes it: `mv` below is atomic
NEW=$WORK/env.new
BACKUP=$WORK/env.previous
if [ -f "$ENV_FILE" ]; then
  cp -p "$ENV_FILE" "$BACKUP"
  awk 'NR == FNR { k = $0; sub(/=.*/, "", k); new[k] = 1; next }
       { k = $0; sub(/=.*/, "", k); if (!(k in new)) print }' "$CHANGES" "$ENV_FILE" >"$NEW"
else
  : >"$BACKUP"
fi
cat "$CHANGES" >>"$NEW"
chmod 600 "$NEW"
mv "$NEW" "$ENV_FILE"

if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans; then
  echo "update: docker compose up failed, previous images restored in $ENV_FILE" >&2
  cp -p "$BACKUP" "$WORK/restore"
  mv "$WORK/restore" "$ENV_FILE"
  # bring the previous images back up (compose may have stopped or half replaced containers); the result is ignored
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans || true
  exit 1
fi

# old images are left untagged by the digest change: free the disk, never fail on it
docker image prune -f >/dev/null 2>&1 || true
echo "update: done"
