#!/bin/sh
# Tests of node/update.sh in POSIX sh (bats is not required).
# `cosign`, `curl` and `docker` are fake scripts placed first on PATH; each one logs its arguments.
#   sh node/test/test_update.sh
# shellcheck disable=SC2319,SC2012,SC2010
# (check receives the exit status of a condition computed in the argument; the file lists are tiny and plain)
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
UPDATE="$HERE/../update.sh"
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT INT TERM

PASS=0
FAIL=0

D1="sha256:1111111111111111111111111111111111111111111111111111111111111111"
D2="sha256:2222222222222222222222222222222222222222222222222222222222222222"
D3="sha256:3333333333333333333333333333333333333333333333333333333333333333"
TANTIVE="ghcr.io/scombat/s1d1-tantive:0.8.0-small-onnx-int8"
CLOUDFLARED="docker.io/cloudflare/cloudflared:2026.10.3"

# ---- fake binaries ----------------------------------------------------------------------------------------------
BIN="$ROOT/bin"
mkdir -p "$BIN"

cat >"$BIN/curl" <<'EOF'
#!/bin/sh
# fake curl: writes $FAKE_DESIRED to the -o file, or fails when FAKE_CURL_FAIL is set
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out=$2; shift; fi
  shift
done
echo "curl" >>"$FAKE_LOG"
[ -z "${FAKE_CURL_FAIL:-}" ] || exit 22
cat "$FAKE_DESIRED" >"$out"
EOF

cat >"$BIN/cosign" <<'EOF'
#!/bin/sh
# fake cosign: logs the call; fails when FAKE_COSIGN_FAIL is set
echo "cosign $*" >>"$FAKE_LOG"
[ -z "${FAKE_COSIGN_FAIL:-}" ] || { echo "error: no matching signatures" >&2; exit 1; }
exit 0
EOF

cat >"$BIN/docker" <<'EOF'
#!/bin/sh
# fake docker: logs the call; `compose up` fails when FAKE_COMPOSE_FAIL is set
echo "docker $*" >>"$FAKE_LOG"
case "$*" in
  *"compose"*" up "*) [ -z "${FAKE_COMPOSE_FAIL:-}" ] || exit 1 ;;
esac
exit 0
EOF
chmod +x "$BIN/curl" "$BIN/cosign" "$BIN/docker"

# ---- helpers ----------------------------------------------------------------------------------------------------
# new_case: fresh node directory with an .env that holds secrets and the images currently running
new_case() {
  CASE=$ROOT/$1
  rm -rf "$CASE"
  mkdir -p "$CASE"
  ENVF=$CASE/.env
  printf 'TUNNEL_TOKEN=tunnel-secret-value\nCOMMUNITY_TOKEN=community-secret-value\n' >"$ENVF"
  : >"$CASE/compose.yaml"
  export FAKE_LOG="$CASE/calls.log"
  export FAKE_DESIRED="$CASE/desired.env"
  unset FAKE_CURL_FAIL FAKE_COSIGN_FAIL FAKE_COMPOSE_FAIL
  : >"$FAKE_LOG"
}

run_update() {
  OUT=$CASE/out.txt
  PATH="$BIN:$PATH" S1D1_COMPOSE_FILE="$CASE/compose.yaml" \
    sh "$UPDATE" "$ENVF" "https://example.invalid/desired.env" >"$OUT" 2>&1
  RC=$?
}

check() { # check <name> <condition exit code>
  if [ "$2" -eq 0 ]; then
    PASS=$((PASS + 1))
    echo "ok   - $1"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL - $1"
    [ ! -f "${OUT:-}" ] || sed 's/^/       | /' "$OUT"
  fi
}

count() { grep -c "$1" "$FAKE_LOG" || true; }

# ---- 1. a signed image: updated, verified, compose called ---------------------------------------------------------
new_case signed
printf '# images\nTANTIVE_IMAGE=%s@%s\nCLOUDFLARED_IMAGE=%s@%s\n' "$TANTIVE" "$D1" "$CLOUDFLARED" "$D2" >"$FAKE_DESIRED"
run_update
check "signed image: exit 0" "$([ "$RC" -eq 0 ]; echo $?)"
check "signed image: env holds the new tantive digest" "$(grep -qx "TANTIVE_IMAGE=$TANTIVE@$D1" "$ENVF"; echo $?)"
check "signed image: env holds the cloudflared digest" "$(grep -qx "CLOUDFLARED_IMAGE=$CLOUDFLARED@$D2" "$ENVF"; echo $?)"
check "signed image: the secrets are kept" "$(grep -qx 'TUNNEL_TOKEN=tunnel-secret-value' "$ENVF" && grep -qx 'COMMUNITY_TOKEN=community-secret-value' "$ENVF"; echo $?)"
check "signed image: tantive verified once, keyless, by digest" "$([ "$(count "cosign verify $TANTIVE@$D1 ")" -eq 1 ] && grep -q 'certificate-identity-regexp.*s1d1-tantive.*build.*yml' "$FAKE_LOG" && grep -q 'certificate-oidc-issuer https://token.actions.githubusercontent.com' "$FAKE_LOG"; echo $?)"
check "signed image: cloudflared is not cosign-verified" "$([ "$(count "cosign.*cloudflared")" -eq 0 ]; echo $?)"
check "signed image: compose up -d called once" "$([ "$(count 'docker compose.* up -d')" -eq 1 ]; echo $?)"
check "signed image: the env file stays private (0600)" "$([ "$(ls -l "$ENVF" | cut -c1-10)" = "-rw-------" ]; echo $?)"
check "signed image: no temporary file left behind" "$([ "$(ls -A "$CASE" | grep -c '^\.\(update\|env\)\.')" -eq 0 ]; echo $?)"
check "signed image: no secret is printed" "$(! grep -q 'secret-value' "$OUT" "$FAKE_LOG"; echo $?)"

# ---- 2. an invalid signature: exit 1, env and compose intact -------------------------------------------------------
new_case badsig
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D1" >>"$ENVF"
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D3" >"$FAKE_DESIRED"
FAKE_COSIGN_FAIL=1
export FAKE_COSIGN_FAIL
run_update
check "invalid signature: exit 1" "$([ "$RC" -eq 1 ]; echo $?)"
check "invalid signature: env unchanged" "$(cmp -s "$ENVF" "$CASE/env.before"; echo $?)"
check "invalid signature: compose never called" "$([ "$(count 'docker')" -eq 0 ]; echo $?)"

# ---- 3. curl fails: exit 2 --------------------------------------------------------------------------------------
new_case netfail
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D1" >"$FAKE_DESIRED"
FAKE_CURL_FAIL=1
export FAKE_CURL_FAIL
run_update
check "network failure: exit 2" "$([ "$RC" -eq 2 ]; echo $?)"
check "network failure: env unchanged" "$(cmp -s "$ENVF" "$CASE/env.before"; echo $?)"
check "network failure: nothing verified or started" "$([ "$(count 'cosign')" -eq 0 ] && [ "$(count 'docker')" -eq 0 ]; echo $?)"

# ---- 4. same digest: no verification and no compose call ---------------------------------------------------------
new_case same
printf 'TANTIVE_IMAGE=%s@%s\nCLOUDFLARED_IMAGE=%s@%s\n' "$TANTIVE" "$D1" "$CLOUDFLARED" "$D2" >>"$ENVF"
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@%s\nCLOUDFLARED_IMAGE=%s@%s\n' "$TANTIVE" "$D1" "$CLOUDFLARED" "$D2" >"$FAKE_DESIRED"
run_update
check "same digest: exit 0" "$([ "$RC" -eq 0 ]; echo $?)"
check "same digest: no cosign call" "$([ "$(count 'cosign')" -eq 0 ]; echo $?)"
check "same digest: no compose call" "$([ "$(count 'docker')" -eq 0 ]; echo $?)"
check "same digest: env unchanged" "$(cmp -s "$ENVF" "$CASE/env.before"; echo $?)"

# ---- 5. a TANTIVE_IMAGE without @sha256: refused -----------------------------------------------------------------
new_case nodigest
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s\n' "$TANTIVE" >"$FAKE_DESIRED"
run_update
check "no digest: exit 1" "$([ "$RC" -eq 1 ]; echo $?)"
check "no digest: nothing verified or started" "$([ "$(count 'cosign')" -eq 0 ] && [ "$(count 'docker')" -eq 0 ]; echo $?)"
check "no digest: env unchanged" "$(cmp -s "$ENVF" "$CASE/env.before"; echo $?)"

new_case placeholder
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@sha256:<to be filled>\n' "$TANTIVE" >"$FAKE_DESIRED"
run_update
check "placeholder digest: exit 1, env unchanged" "$([ "$RC" -eq 1 ] && cmp -s "$ENVF" "$CASE/env.before"; echo $?)"

# ---- 6. beyond the brief ------------------------------------------------------------------------------------------
new_case foreignkey
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@%s\nTUNNEL_TOKEN=attacker\n' "$TANTIVE" "$D1" >"$FAKE_DESIRED"
run_update
check "a key that is not *_IMAGE is refused (exit 1, nothing changes)" "$([ "$RC" -eq 1 ] && cmp -s "$ENVF" "$CASE/env.before" && [ "$(count 'cosign')" -eq 0 ]; echo $?)"

new_case composefail
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D1" >>"$ENVF"
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D3" >"$FAKE_DESIRED"
FAKE_COMPOSE_FAIL=1
export FAKE_COMPOSE_FAIL
run_update
check "compose failure: exit 1 and the previous env is restored (retried next run)" "$([ "$RC" -eq 1 ] && cmp -s "$ENVF" "$CASE/env.before"; echo $?)"
check "compose failure: compose runs once more on the restored env" "$([ "$(count 'docker compose.* up -d')" -eq 2 ]; echo $?)"

new_case cloudflared-only
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D1" >>"$ENVF"
printf 'TANTIVE_IMAGE=%s@%s\nCLOUDFLARED_IMAGE=%s@%s\n' "$TANTIVE" "$D1" "$CLOUDFLARED" "$D3" >"$FAKE_DESIRED"
run_update
check "cloudflared digest change: applied without cosign, compose called" "$([ "$RC" -eq 0 ] && grep -qx "CLOUDFLARED_IMAGE=$CLOUDFLARED@$D3" "$ENVF" && [ "$(count 'cosign')" -eq 0 ] && [ "$(count 'docker compose.* up -d')" -eq 1 ]; echo $?)"

new_case partial
printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D1" >>"$ENVF"
cp "$ENVF" "$CASE/env.before"
printf 'TANTIVE_IMAGE=%s@%s\nCLOUDFLARED_IMAGE=docker.io/cloudflare/cloudflared:latest\n' "$TANTIVE" "$D3" >"$FAKE_DESIRED"
run_update
check "one bad line refuses the whole file (exit 1, nothing verified, env unchanged)" "$([ "$RC" -eq 1 ] && cmp -s "$ENVF" "$CASE/env.before" && [ "$(count 'cosign')" -eq 0 ]; echo $?)"

# ---- 7. images only from their own repositories ------------------------------------------------------------------
refused() { # refused <name> <desired.env line>: exit 1, env untouched, nothing verified or started
  new_case "$1"
  printf 'TANTIVE_IMAGE=%s@%s\n' "$TANTIVE" "$D1" >>"$ENVF"
  cp "$ENVF" "$CASE/env.before"
  printf '%s\n' "$2" >"$FAKE_DESIRED"
  run_update
  check "$1: exit 1, env unchanged, no cosign, no compose" "$([ "$RC" -eq 1 ] && cmp -s "$ENVF" "$CASE/env.before" && [ "$(count 'cosign')" -eq 0 ] && [ "$(count 'docker')" -eq 0 ]; echo $?)"
}
refused tantive-foreign-registry "TANTIVE_IMAGE=docker.io/attacker/s1d1-tantive:0.8.0@$D3"
refused tantive-foreign-owner "TANTIVE_IMAGE=ghcr.io/attacker/s1d1-tantive:0.8.0@$D3"
refused tantive-foreign-repo "TANTIVE_IMAGE=ghcr.io/scombat/other:0.8.0@$D3"
refused tantive-no-registry "TANTIVE_IMAGE=scombat/s1d1-tantive:0.8.0@$D3"
refused cloudflared-foreign-registry "CLOUDFLARED_IMAGE=docker.io/attacker/x:1@$D3"
refused cloudflared-other-registry "CLOUDFLARED_IMAGE=ghcr.io/cloudflare/cloudflared:2026.10.3@$D3"
refused cloudflared-implicit-registry "CLOUDFLARED_IMAGE=cloudflare/cloudflared:2026.10.3@$D3"
refused other-image-key "FOO_IMAGE=docker.io/cloudflare/cloudflared:2026.10.3@$D3"
refused tag-with-extra-at "TANTIVE_IMAGE=$TANTIVE@$D1@$D3"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
