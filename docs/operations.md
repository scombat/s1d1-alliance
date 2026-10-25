# Operations guide

How to bring up and run `s1d1.qoo.gy`. Everything below is done by the maintainer, by hand; the repository holds no
secret. Component details live next to the code: [terraform/README.md](../terraform/README.md),
[node/README.md](../node/README.md), [anonymizer/README.md](../anonymizer/README.md),
[workers/site/README.md](../workers/site/README.md), [workers/ingest/README.md](../workers/ingest/README.md).

## 1. Manual prerequisites (decision 50)

Done once, outside Terraform:

1. **Cloudflare**: the account, the zone `qoo.gy`, R2 enabled on the account, **Analytics Engine enabled once** in the
   dashboard (Workers & Pages, Analytics Engine; otherwise the first apply fails with error 10089 on the `VISITS`
   binding), and a provider token with the permissions listed in `terraform/README.md` ("Token permissions"). Terraform
   manages no zone ruleset: the zone's single free rate limiting rule belongs to another subdomain.
2. **Google Cloud**: a project with billing linked; `serviceusage.googleapis.com` and
   `cloudresourcemanager.googleapis.com` enabled; a service account for HCP with the roles listed in
   `terraform/README.md` ("Manual steps", item 3), including **`roles/storage.hmacKeyAdmin`** (`roles/storage.admin`
   does not grant `storage.hmacKeys.create`) and a billing role **on the billing account**. It is the most powerful
   credential of the project: keep it out of the repository and rotate it. The recommended way to give it to HCP is
   dynamic provider credentials (workload identity pool `hcp-terraform`, with a condition on organization `scombat`
   and workspace `s1d1-alliance`); the maintainer used that path.
3. **HCP Terraform**: an organisation (the workspace is created below).
4. **Hetzner Cloud**: an account and a project, and in that project an API token with **Read & Write** permission
   (Security, API tokens), set as `hcloud_token` (sensitive) in HCP. The node is a paid server (about 6 EUR excluding
   VAT a month): the Hetzner project has its own billing, the Google Cloud budget does not cover it.
5. **GitHub**: the repository `scombat/s1d1-alliance`. Terraform creates the federation that lets its `main` branch push
   the anonymizer image; the three repository variables of section 3 are set by hand.

## 2. HCP Terraform workspace

1. Create the workspace `s1d1-alliance` (the name in `terraform/versions.tf`), VCS-driven, connected to the repository.
2. Settings, General: **Terraform Working Directory = `terraform`**, and keep the **VCS root = the repository root**.
   The configuration reads `../workers/*` and `../site`; a workspace that uploads only `terraform/` fails at plan.
3. Set the workspace variables below. Sensitive ones are marked as such in HCP (write-only afterwards).
   Google credentials, recommended: dynamic provider credentials, i.e. the environment variables
   `TFC_GCP_PROVIDER_AUTH` = `true`, `TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL` and `TFC_GCP_WORKLOAD_PROVIDER_NAME` (pool
   `hcp-terraform`, condition on organization `scombat` and workspace `s1d1-alliance`). Alternative: `GOOGLE_CREDENTIALS`
   (environment variable, sensitive), a long-lived key.

| variable | sensitive | note |
|---|---|---|
| `cloudflare_account_id` | no | |
| `cloudflare_zone_id` | no | zone `qoo.gy` |
| `cloudflare_api_token` | **yes** | |
| `gcp_project` | no | also names the raw bucket `<project>-s1d1-raw` |
| `billing_account` | no | |
| `budget_currency` | no | must equal the billing account currency (default `EUR`) |
| `anonymizer_image` | no | not set by hand: it comes from `terraform/images.auto.tfvars` (section 3); absent (null) on the first apply |
| `hcloud_token` | **yes** | Read & Write API token of the Hetzner Cloud project |
| `TFC_GCP_PROVIDER_AUTH`, `TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL`, `TFC_GCP_WORKLOAD_PROVIDER_NAME` | no | environment variables of the dynamic provider credentials (recommended) |
| `GOOGLE_CREDENTIALS` | **yes** | environment variable, only if not using dynamic credentials |

Optional, with defaults: `deploy_version`, `status_commit`, `tantive_image`, `status_model`, `maintenance`, `cache_ttl`,
`debug_cache_header`, `origin_requires_auth`, `gcp_region`, `budget_amount`, `github_repository`, `github_repository_owner_id` (set it: the numeric owner id),
`hcloud_location`, `hcloud_server_type`.

## 3. First apply, in this order

The Cloud Run service needs an image that already exists, and the image needs the registry and the federation. The
workspace is VCS-driven, so there is no `-target`: `anonymizer_image` is null by default, and with it null Terraform
creates everything except the anonymizer (no Cloud Run service, no Eventarc trigger, none of the anonymizer's IAM
bindings). So the first apply is a plain run, and a second one, with the image set, adds the anonymizer.

1. **First apply, without the anonymizer**: start a run of the workspace (or merge to `main`) with no
   `terraform/images.auto.tfvars`. It creates the APIs, Artifact Registry, the federation and `ci-publisher`, the raw
   bucket, the secrets, the budget, the R2 bucket and its token, the Workers, the tunnel and the Hetzner node. Until the
   second apply, `/ingest` writes raw objects that nobody cleans; the raw bucket deletes them at 3 days.
2. **Repository variables** (GitHub, Settings, Secrets and variables, Actions, Variables; none is a secret):
   `GCP_WIF_PROVIDER` = `terraform output -raw ci_workload_identity_provider`,
   `GCP_CI_SA` = `terraform output -raw ci_publisher_service_account`, `GCP_PROJECT` = the project id, and
   `GCP_REGION` only if `gcp_region` is not the default `europe-west1` (the CI pushes to
   `<GCP_REGION>-docker.pkg.dev`, `europe-west1` when it is unset).
3. **Push the image**: Actions, `ci`, "Run workflow" on `main` (`workflow_dispatch`; the `anonymizer-image` job otherwise
   runs only on a push to `main` that changes `anonymizer/`). It builds `linux/amd64`, pushes
   `<region>-docker.pkg.dev/<project>/s1d1/anonymizer:<commit sha>` and prints, in the job summary, the line to paste. It
   never commits.
4. **Set the digest**: in a pull request, create `terraform/images.auto.tfvars` with that line:
   `anonymizer_image = "<region>-docker.pkg.dev/<project>/s1d1/anonymizer@sha256:<64 hex>"`.
   (`*.auto.tfvars` is the one tfvars form git keeps.) A tag without a digest is refused by the variable.
5. **Second apply**: merging the pull request starts it. It adds the Cloud Run service, its invoker binding, the
   anonymizer's bucket and secret bindings, the Eventarc receiver role and the trigger. The Cloud Storage service
   agent's publisher role is created by the first apply so that it has propagated by then; if the trigger is still
   refused with a permission error on the service agent (it can take a few minutes the first time), start the run
   again: the second try succeeds once the role has propagated. Terraform also grants the Eventarc and Pub/Sub service
   agents their roles (`roles/eventarc.serviceAgent`, `roles/iam.serviceAccountTokenCreator`); if an agent does not
   exist yet, `gcloud beta services identity create --service=eventarc.googleapis.com` (or `pubsub.googleapis.com`)
   creates it. Every later image goes the same way: merge to `main`,
   read the summary, open a PR with the new digest, apply.
6. The tunnel exists with no connector: start the nodes (section 5). Fill `status_commit`, `tantive_image` and
   `status_model` for `/status.json` when the node digests are known.

Artifact Registry has no cleanup policy: an age rule would one day delete the image Cloud Run runs. Delete old images
by hand when the storage cost matters, never the digest in `terraform/images.auto.tfvars`.

## 4. Cloudflare, by hand (Terraform cannot)

- **Route failure mode** (`cloudflare_workers_route` has no such field). Dashboard: Workers & Pages, the Worker,
  Settings, Domains & Routes, the route, "Failure mode".
  - `s1d1.qoo.gy/*` (the `s1d1-site` Worker): **fail open** (docs/26 decision 22). If the Worker cannot run
    (for example its quota is exhausted), the request goes straight to the tunnel. This is safe because the tunnel's own
    ingress still lists only the API paths and answers 404 for everything else, `/metrics` included; what is lost is the
    cache, the maintenance switch and the landing, which is accepted.
  - `s1d1.qoo.gy/ingest` (the `s1d1-ingest` Worker): **fail closed**. Failing open would send `/ingest` to a
    tunnel that does not serve it; closed returns a Cloudflare 1027 page and tantive retries.
  Check both after every apply (open question 4 of docs/26).
- **workers.dev**: both Workers have it disabled by Terraform. If the apply fails on those resources because the account
  has no workers.dev subdomain, see `terraform/README.md`.
- **Cloudflare Access** (optional, for `/metrics`, section 8): an Access application on the tunnel hostname you choose.

## 5. Nodes

### Hetzner node

Oracle Always Free was dropped (account creation refused); the second node is a Hetzner VM.

Created by Terraform (`hetzner.tf`): a `cax11` Arm server with a firewall that has no inbound rule, no SSH key, SSH
stopped and masked by cloud-init. It writes `/opt/s1d1/.env` (0600) from cloud-init, installs cosign and the timer, and
follows `node/desired.env` on `main` by itself. To look inside, use the Hetzner Console (web VNC) of the server. A node
you cannot repair is replaced: `terraform apply -replace=hcloud_server.node`. If the apply fails for lack of Arm
capacity, change `hcloud_location` (`fsn1`, `nbg1`, `hel1`: EU only, on purpose) or retry later. If CAX stays
unavailable, set `hcloud_server_type` to a shared x86 type with 4 GB of RAM: `cx23` (2 vCPU, 40 GB) or `cpx22`
(2 AMD vCPU, 80 GB), listed by Hetzner for `nbg1` and `hel1`; the image and the cloud-init follow the architecture.
The root password Hetzner e-mails only works on the web console (SSH is masked): change it at the first console login.

### Homelab

1. `mkdir -p /opt/s1d1` and copy `node/compose.yaml`, `node/update.sh`, `node/s1d1-update.service`,
   `node/s1d1-update.timer` there.
2. `cp node/example.env /opt/s1d1/.env && chmod 0600 /opt/s1d1/.env`, then fill the two secrets from the workspace
   outputs, read on a trusted machine and never written to chat or a ticket:
   `TUNNEL_TOKEN` = `terraform output -raw tunnel_token`, `COMMUNITY_TOKEN` = `terraform output -raw community_token`.
   Leave the two `*_IMAGE` lines: `update.sh` rewrites them from `desired.env`.
3. Install cosign (version and checksum as in `node/cloud-init.yaml.tftpl`), `docker.io` and `docker-compose-v2`.
4. Install the units from `node/` into `/etc/systemd/system/`, then `systemctl daemon-reload` and
   `systemctl enable --now s1d1-update.timer`. First run now: `/opt/s1d1/update.sh /opt/s1d1/.env <raw URL of desired.env>`
   (the URL is the one in `s1d1-update.service`:
   `https://raw.githubusercontent.com/scombat/s1d1-alliance/main/node/desired.env`).
5. Check: `docker compose -f /opt/s1d1/compose.yaml --env-file /opt/s1d1/.env ps`, and `journalctl -u s1d1-update`.
   `desired.env` pins tantive 0.8.0 by digest. A value that is not `sha256:<64 hex>` (a placeholder, a tag alone) is
   refused at `update.sh`'s format check (exit 1) before any cosign verification, and the node keeps what it runs.

## 6. Emergency stop: `MAINTENANCE`

Set the workspace variable `maintenance` to `1` and apply. `/api/*` and `/ingest` answer 503
`{"error": "maintenance"}` with `Retry-After: 3600`; the landing stays up, and nothing is served from the cache during
maintenance. Back to `0` and apply to resume. tantive retries a 503 from `/ingest`, so nothing is lost on the nodes
within their two-day capture window. To stop the anonymizer itself too, set its Cloud Run maximum instances to 0 (the
raw bucket keeps filling for at most 3 days, then deletes; see the next section).

## 7. Daily check of the raw bucket (decision 52)

The raw bucket deletes at 3 days. **Nothing may be older than 4 days**; an older object means the lifecycle rule or the
anonymizer is broken and raw prompts are being kept too long.

```
gcloud storage ls -l -r "gs://<project>-s1d1-raw/raw/" \
  | awk -v limit="$(date -u -d '4 days ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-4d +%Y-%m-%dT%H:%M:%SZ)" \
      '$3 ~ /^gs:/ && $2 < limit { print "TOO OLD:", $2, $3; bad=1 } END { exit bad }'
```

Exit status 0 and no output: fine. Otherwise look at the Eventarc trigger and Cloud Run errors first. (`gcloud storage ls -l` prints size, creation time and URL; run it once by hand
to confirm the format of your `gcloud` version.) Also glance at the
clean side: an object `clean/YYYY/MM/DD/<uuid>.ndjson` must exist for each recent raw one.

## 8. Reading `/metrics` (decision 49)

`/api/metrics` answers 404 on the public name, by design (closed list). Read the metrics only from the node itself. The
Compose file publishes no port and the tantive image has python but no curl, so on the homelab:

```
docker compose -f /opt/s1d1/compose.yaml --env-file /opt/s1d1/.env exec tantive \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8788/metrics').read().decode())"
```

(The command calls `/metrics` inside the container, without the public `/api` prefix; check it once on the first node,
since only the prefixed API paths are proven by the smoke test of section 9.1.) Or go through a
Cloudflare Access application in front of a separate private hostname of the tunnel. Never open a public path for them.

## 9. Smoke tests after the first deploy

These were verified against documentation only; the first deploy is where they are proven.

1. **Same-zone fetch and the `/api` prefix, end to end**: `POST https://s1d1.qoo.gy/api/v1/systemone` with a
   System One body must answer 200 with a decision. That proves the `s1d1-site` Worker's subrequest reaches the tunnel
   (the origin) instead of looping back into the Worker, and that tantive answers a path that starts with `/api`.
   `S1D1_ROOT_PATH=/api` alone does not make that work: the request reaches tantive with the prefix still in its path,
   and tantive 0.8.0 and later handle a request path that starts with the prefix (fixed in s1d1-tantive PR #5). To see
   the cache, set `debug_cache_header` to `1` and apply: `x-s1d1-cache` then shows `MISS`, then `HIT` on the same
   `POST` repeated; set it back to `0` afterwards (the header is absent by default).
2. **Worker module edits show in plan**: change a non-index module (for example `workers/site/src/api.js`) and
   check that `terraform plan` shows a diff on the script.
3. **R2 EU endpoint write**: the anonymizer writes `clean/...` to `r2_endpoint` (the `eu` jurisdiction endpoint); a
   test object appears in `s1d1-clean`.
4. **GCS PUT from the ingest Worker**: `POST /ingest` with the fixtures of `contract/fixtures/` gives 202, and a
   `raw/YYYY/MM/DD/<uuid>.ndjson` object appears.
5. **Eventarc reaches the `INTERNAL_ONLY` Cloud Run service**: that object produces a clean one within a minute or two. If
   Cloud Run logs show 403/404 deliveries, see "Ingress INTERNAL_ONLY and Eventarc" in `terraform/README.md` for the
   fallback.
6. **`/api` rate limit (Worker binding `API_BURST`)**: check that the apply accepts the `ratelimit` binding of
   `s1d1-site` (namespace id 1003). Then send 101 `GET /api/v1/models` in 10 s from one IP: the last ones get 429
   `rate_limited` with `Retry-After: 10` and never reach the origin, while `GET /` and `/status.json` stay 200. The
   limit is approximate and per location, so expect a few requests of margin, and the Worker's own subrequest to the
   origin must not double the count. The zone has no rate limit rule of ours: do not look for one in the dashboard
   (the single rule there is the other subdomain's). Known trade-off: when the Workers daily request quota is
   exhausted the route fails open and `/api/*` goes to the tunnel with no rate limit.
7. **Peak memory of the anonymizer**: after a real batch, read the peak memory of the Cloud Run revision (metrics
   `container/memory/utilizations`) and compare with the limit. Done 2026-10-25: 4.1 to 4.6 GiB with both models, so
   4 GiB was killed (signal 9) and Eventarc retried; the service now has 8 GiB and 2 vCPU.
8. **Ingest rate limits (to verify at the first apply)**: the Cloudflare page on the Workers Rate Limiting binding does
   not say whether it is available on the Workers Free plan; check that the apply accepts the two `ratelimit`
   bindings of `s1d1-ingest` (namespace ids 1001 and 1002). Then, with `contract/fixtures/valid.ndjson` and no
   token, send 101 `POST /ingest` in 10 s from one IP: the 101st gets 429 `rate_limited` with `Retry-After: 10`, and
   the same burst with the community token stays 202. The limits are approximate and per location, so expect a
   few requests of margin.

## 10. Known corpus cost: script coverage and the drop rule

The anonymizer masks only the scripts it has been shown to cover. A prompt holding a single letter in an uncovered script
(Cyrillic, Japanese, Devanagari, Thai and any script not tested) is **dropped, not stored**, and counted as
`dropped_uncovered_script`. The clean corpus is therefore biased toward Latin-script prompts. Which scripts are covered,
the evidence, and the exact rule are in [anonymizer/README.md](../anonymizer/README.md#scripts-covered-or-dropped). This is
a deliberate privacy-over-coverage choice: say it when presenting the corpus.

## 11. Questions still open

From docs/26 section 5, after the checks of tasks 9 and 10 (the full text is in the s1d1-model-router repository):

- **Question 4, route failure mode**: only the dashboard can set it; the values of section 4 must be checked after each
  apply.
- **Live behaviours not provable offline**: the eight smoke tests of section 9 (same-zone fetch, `INTERNAL_ONLY`
  Eventarc, R2 EU write, GCS PUT, `/api` rate limit, peak memory, ingest rate limits).
- **Eventarc retries**: the policy cannot be tuned in this provider version (`max_attempts = 1` only, no dead letter);
  a permanently failing object can be redelivered for 24 h. Watch Cloud Run's error rate.
- **Legal texts**: the three French pages are templates until the lawyer has written and reviewed them; the licence of this
  repository is the maintainer's decision.
- **tantive 0.8.0 digests**: `node/desired.env` and `node/example.env` carry placeholders until it is published.
- **Cache lifetime**: `CACHE_TTL` is 86 400 s by the builder's choice (docs/26 section 4); revisit with real traffic.
