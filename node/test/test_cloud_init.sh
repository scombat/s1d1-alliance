#!/bin/sh
# Checks that node/cloud-init.yaml.tftpl embeds node/'s files verbatim (Terraform escapes undone), that the only
# template variables are the three documented ones, and that the secrets file is private.
#   sh node/test/test_cloud_init.sh
set -u

HERE=$(cd "$(dirname "$0")/.." && pwd)
TPL=$HERE/cloud-init.yaml.tftpl
URL='https://raw.githubusercontent.com/scombat/s1d1-alliance/main/node/desired.env'
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
FAIL=0

fail() { echo "FAIL - $1"; FAIL=$((FAIL + 1)); }
ok() { echo "ok   - $1"; }

# extract <path>: the body of the write_files entry, unindented and with the Terraform escapes undone,
# the three template variables put back to their documented values
extract() {
  awk -v p="$1" '
    $0 == "  - path: " p { f = 1; next }
    f == 1 && $0 == "    content: |" { f = 2; next }
    f == 2 { if ($0 ~ /^      / || $0 == "") print substr($0, 7); else exit }
  ' "$TPL" | sed -e 's/[$][$]{/${/g' -e 's/%%{/%{/g' -e "s|[$]{desired_url}|$URL|"
}

same() { # same <path in the node> <file of node/>
  extract "$1" >"$WORK/body"
  if [ -s "$WORK/body" ] && cmp -s "$WORK/body" "$HERE/$2"; then
    ok "$1 is a verbatim copy of node/$2"
  else
    fail "$1 differs from node/$2 (or is missing)"
    diff "$WORK/body" "$HERE/$2" | head -5 | sed 's/^/       | /'
  fi
}

same /opt/s1d1/compose.yaml compose.yaml
same /opt/s1d1/cosign.pub cosign.pub
same /opt/s1d1/update.sh update.sh
same /etc/systemd/system/s1d1-update.service s1d1-update.service
same /etc/systemd/system/s1d1-update.timer s1d1-update.timer

vars=$(sed -e 's/[$][$]{//g' "$TPL" | grep -o '[$]{[^}]*}' | sort -u | tr '\n' ' ')
# shellcheck disable=SC2016  # the single quotes are meant: the variables are literal text
if [ "$vars" = '${community_token} ${desired_url} ${tunnel_token} ' ]; then
  ok "the only template variables are tunnel_token, community_token, desired_url"
else
  fail "unexpected template variables: $vars"
fi

if sed -e 's/%%{//g' "$TPL" | grep -q '%{'; then
  fail "an unescaped Terraform directive (percent-brace) is present"
else
  ok "no unescaped Terraform directive"
fi

if awk '$0 == "  - path: /opt/s1d1/.env" { f = 1; next } f && /permissions:/ { print; exit }' "$TPL" | grep -q '"0600"'; then
  ok "/opt/s1d1/.env is written with mode 0600"
else
  fail "/opt/s1d1/.env is not mode 0600"
fi

for cmd in 'sha256sum -c' 'cosign-linux-' 'systemctl enable --now s1d1-update.timer' 'systemctl start s1d1-update.service'; do
  if grep -q "$cmd" "$TPL"; then ok "runcmd has: $cmd"; else fail "runcmd lacks: $cmd"; fi
done

echo
[ "$FAIL" -eq 0 ] && echo "all passed" || echo "$FAIL failed"
[ "$FAIL" -eq 0 ]
