# terraform

Infrastructure of the public instance `s1d1.qoo.gy`, in HCP Terraform (workspace `s1d1-alliance`).
This folder holds the Cloudflare part (tunnel, DNS, Workers, routes, rate limit, R2) and the Google Cloud part (raw
bucket, service accounts, anonymizer on Cloud Run, Eventarc, budget, CI identity): `gcp.tf`, `budget.tf`, `wif.tf`.

Providers are pinned exactly: `cloudflare/cloudflare = 5.28.1`, `hashicorp/google = 8.8.1`, `hashicorp/random = 3.9.1`, `oracle/oci = 9.11.0`
(no `google-beta`: every resource used is GA). The lock file
`.terraform.lock.hcl` is committed with the hashes for `linux_amd64` (CI, HCP) and `darwin_arm64` (laptops).
To add a platform or upgrade: `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64`.

## HCP Terraform workspace

`versions.tf` has `cloud { workspaces { name = "s1d1-alliance" } }`. The organisation is not in the code:

```
export TF_CLOUD_ORGANIZATION=<your organisation>
terraform login            # or TF_TOKEN_app_terraform_io
terraform init
```

**The configuration reads files outside `terraform/`**: the Worker sources (`../workers/site/src`,
`../workers/ingest/src`, `../workers/ingest/vendor`) and the static site (`../site`). So the HCP workspace must be
VCS-driven with the **repository root as VCS root and Terraform Working Directory = `terraform`** (Settings, General,
"Terraform Working Directory"): the whole repository is then checked out for each run. A workspace whose upload
contains only `terraform/` fails at plan with "no such file". For a CLI-driven run, start it **from the repository
root** with the working directory set the same way (`TF_WORKSPACE`/the `cloud` block, then `terraform -chdir=terraform
plan`), so that the CLI uploads the whole repository, not only `terraform/`.

The workspace runs remotely, so the variables below are set on the workspace (sensitive ones marked as such), not in
a file. `*.tfvars` is ignored by git; never commit one.

Checks that need no account (what CI runs):

```
terraform fmt -check -recursive
terraform init -backend=false     # the `cloud` block does not get in the way
terraform validate
```

## Variables

| name | sensitive | default | role |
|---|---|---|---|
| `cloudflare_account_id` | no | none | account that owns the tunnel, the Workers, R2 |
| `cloudflare_zone_id` | no | none | zone `qoo.gy` |
| `cloudflare_api_token` | yes | none | token of the provider (permissions below) |
| `deploy_version` | no | `v1` | `DEPLOY_VERSION`, part of the cache key: bump it to empty the cache |
| `status_commit` | no | `unknown` | `STATUS_COMMIT` of `/status.json` |
| `tantive_image` | no | `unknown` | `STATUS_TANTIVE_IMAGE` of `/status.json` |
| `status_model` | no | `unknown` | `STATUS_MODEL` of `/status.json` |
| `maintenance` | no | `0` | `"1"`: `/api/*` and `/ingest` answer 503, the landing stays up |
| `cache_ttl` | no | `86400` | `CACHE_TTL` in seconds, a whole number greater than 0 |
| `debug_cache_header` | no | `0` | `DEBUG_CACHE_HEADER`: `"1"` adds `x-s1d1-cache: HIT`/`MISS` to cached API answers (smoke tests only) |
| `origin_requires_auth` | no | `0` | `ORIGIN_REQUIRES_AUTH`: `"1"` when the origin gates decisions behind a token; the site Worker then bypasses the cache |
| `gcp_project` | no | none | Google Cloud project id; also names the raw bucket `<project>-s1d1-raw` (bucket names are global) |
| `gcp_region` | no | `europe-west1` | bucket, Cloud Run, Artifact Registry, secrets, Eventarc trigger |
| `billing_account` | no | none | billing account id (`000000-000000-000000`) for the budget |
| `budget_amount` | no | `5` | monthly budget, whole units |
| `budget_currency` | no | `EUR` | must be the currency of the billing account |
| `anonymizer_image` | no | null | `<region>-docker.pkg.dev/<project>/s1d1/anonymizer@sha256:<64 hex>`, built by CI from this repo; a tag without a digest is refused. Null: no Cloud Run service, no Eventarc trigger, no anonymizer IAM bindings (first apply) |
| `github_repository` | no | `scombat/s1d1-alliance` | the repository whose `main` branch may push images |
| `github_repository_owner_id` | no | empty | optional numeric id of the GitHub owner, added to the federation condition |
| `oci_tenancy_ocid` | no | none | Oracle Cloud tenancy |
| `oci_user_ocid` | no | none | Oracle Cloud user whose API key Terraform uses |
| `oci_fingerprint` | no | none | fingerprint of that API key |
| `oci_private_key` | yes | none | PEM content of the API key (not a path) |
| `oci_region` | no | `eu-paris-1` | region (the home region, for Always Free) |
| `oci_compartment_ocid` | no | none | compartment of the network and the instance |
| `oci_availability_domain_index` | no | `0` | availability domain (0, 1, 2) of the instance: see "Oracle node" |

The GCS values of the ingest Worker (`RAW_BUCKET`, `GCS_HMAC_ID`, `GCS_HMAC_SECRET`) are not variables: they are
`local.raw_bucket_name`, `local.gcs_hmac_id` and `local.gcs_hmac_secret`, defined in `gcp.tf` from the raw bucket and
the HMAC key of `ingest-writer`. The R2 credentials of the anonymizer are not variables either: the secrets are made
from the R2 API token of `cloudflare.tf`.

## Outputs (all sensitive except `r2_endpoint`)

| name | what |
|---|---|
| `tunnel_token` | for `cloudflared tunnel run --token` on the nodes |
| `community_token` | `S1D1_CAPTURE_TOKEN` of the community node, `COMMUNITY_TOKEN` of the ingest Worker |
| `r2_access_key_id` | S3 access key id of `s1d1-clean`: the id of the API token |
| `r2_secret_access_key` | S3 secret: the SHA-256 of the API token value |
| `r2_endpoint` | `https://<account>.eu.r2.cloudflarestorage.com` (EU jurisdiction: a jurisdictional bucket is only reachable on its own endpoint) |

Four outputs of the Google part are not sensitive: `ci_workload_identity_provider` and `ci_publisher_service_account`
(the two inputs of `google-github-actions/auth`), `artifact_registry_repository` (where to push) and
`anonymizer_url` (null until `anonymizer_image` is set). No GCP secret is an output; the HMAC secret only goes to the ingest Worker binding.

`node_public_ip` (not sensitive) is the Oracle node's address; nothing listens on it.

Read one with `terraform output -raw community_token` (or in the workspace page).

## What is created

- Tunnel `s1d1` (remotely managed) and its ingress: `s1d1.qoo.gy`, path
  `^/api/(v1/(chat/completions|models|decisions|systemone(/batch)?)|classify|predict|models)$` to
  `http://tantive:8788`, then `http_status:404`. No path is rewritten: tantive receives `/api/...`. `S1D1_ROOT_PATH=/api`
  alone does not make that work; tantive 0.8.0 and later handle a request path that starts with the prefix (fixed in
  s1d1-tantive PR #5), and smoke test 9.1 of `docs/operations.md` checks `POST /api/v1/systemone` end to end.
- `CNAME s1d1` to `<tunnel id>.cfargotunnel.com`, proxied.
- Workers `s1d1-site` (static files of `../site`, Analytics Engine binding `VISITS` on dataset `s1d1_visits`) and
  `s1d1-ingest`; routes `s1d1.qoo.gy/*` to the first and `s1d1.qoo.gy/ingest` to the second.
- Ruleset `http_ratelimit`: 100 requests per 10 s per IP on `starts_with(http.request.uri.path, "/api/")`, action
  `block`, blocked for 10 s.
- Two `ratelimit` bindings on `s1d1-ingest` (`INGEST_BURST` 100 per 10 s, namespace 1001; `INGEST_SUSTAINED` 100 per
  60 s, namespace 1002), per client IP, community node exempt: see `workers/ingest/README.md`. They replace the zone
  rule for `/ingest`, which covers `/api/` only.
- R2 bucket `s1d1-clean` (jurisdiction `eu`) and an API token limited to it, with the one permission group
  `Workers R2 Storage Bucket Item Write`: the anonymizer only writes (`PutObject`), so `Item Read` is not granted. R2
  has no write-only object permission: `Item Write` is the dashboard's "Object Read & Write" and also allows reading
  and listing that bucket's objects (https://developers.cloudflare.com/r2/api/tokens/). The bucket scope is the limit.
- `random_password.community_token` (48 characters, no special character).
- `cloudflare_workers_script_subdomain` with `enabled = false` for both Workers, so that they are served by the
  routes only (a workers.dev copy of the site Worker would not go through the zone's rate limit). If the apply
  fails on these two resources because the account has no workers.dev subdomain registered, either register one
  once (dashboard: Workers & Pages, "Your subdomain"; nothing is exposed since the Workers are then disabled on
  it) and re-run, or remove the two `cloudflare_workers_script_subdomain` blocks from `workers.tf` and check by hand
  that `s1d1-site.<subdomain>.workers.dev` does not answer.

A change to any Worker file or to `../site` redeploys: the provider hashes at plan time each `files` entry
(`files.<name>.content_sha256`, computed from `content_file`) and the whole asset folder
(`assets.asset_manifest_sha256`, from `assets.directory`), and the main module through `content_sha256`, so editing
`api.js`, `cache.js`, `gcs.js`, `validate.js`, `vendor/aws4fetch.js` or any site file shows as a diff. Nothing else
is needed (see the comment in `workers.tf`).

How the Workers are uploaded: no build and no bundler. `content_file` is the main module (`src/index.js`) and the
other modules are `files` entries named by their path (`src/api.js`, `vendor/aws4fetch.js`, ...), so the relative
imports resolve inside the upload as they do on disk. The static assets are uploaded from `../site` by the
provider (`assets.directory`); `run_worker_first = ["/", "/status.json", "/api/*"]`.

## What is created on Google Cloud

```
ingest Worker --HMAC--> raw bucket --object finalized--> Eventarc --OIDC--> anonymizer --S3--> R2 s1d1-clean
```

- **APIs** (`google_project_service`, never disabled on destroy): run, eventarc, pubsub, storage, secretmanager,
  artifactregistry, iam, iamcredentials, sts, billingbudgets, cloudresourcemanager (`iam` is not in the brief but the
  service accounts need it).
- **Raw bucket** `<project>-s1d1-raw`, region `europe-west1`, uniform bucket-level access, public access prevention
  `enforced`, objects deleted at age 3 days. The soft-delete window is set to 0: the default 7 days would keep the
  deleted raw prompts recoverable for a week more.
- **Service accounts**, one role each, all on the bucket or secret concerned and none at project level except the two
  Eventarc ones:
  - `ingest-writer`: `roles/storage.objectCreator` on the raw bucket only; an HMAC key for the Worker. It can neither
    read, list, overwrite nor delete.
  - `anonymizer`: `roles/storage.objectViewer` on the raw bucket only, `roles/secretmanager.secretAccessor` on the
    two R2 secrets only; it is the identity of the Cloud Run service.
  - `eventarc-invoker`: `roles/run.invoker` on the service only, `roles/eventarc.eventReceiver` on the project.
  - `ci-publisher`: `roles/artifactregistry.writer` on the `s1d1` repository only (see below).
- **Secrets** `r2-access-key-id`, `r2-secret-access-key` in Secret Manager, replicated in `europe-west1` only. The
  service reads the pinned version, so a rotation (new R2 token) makes a new revision.
- **Artifact Registry** `s1d1` (Docker), with **no cleanup policy**: a DELETE rule by age would delete the deployed
  image when no new one was pushed for that long (Cloud Run could then not start a new instance), and a KEEP rule
  alone deletes nothing. Delete old images by hand (`gcloud artifacts docker images list` / `delete`) when the storage
  cost matters, never the digest in `terraform/images.auto.tfvars`.
- **Created only when `anonymizer_image` is set** (the second apply of `docs/operations.md`, section 3): the Cloud Run
  service, its `run.invoker` binding, the anonymizer's bucket and secret bindings, `roles/eventarc.eventReceiver` of
  `eventarc-invoker` and the Eventarc trigger. The GCS service agent's `roles/pubsub.publisher` is created by the first
  apply, so that it has propagated when the trigger is created; a trigger refused on the service agent's permission
  the first time succeeds on the next run.
- **Cloud Run** `s1d1-anonymizer`: internal ingress, 0 to 2 instances, 1 vCPU, **4 GiB**, concurrency 1, timeout 3600 s,
  CPU only during requests, startup CPU boost. Environment: `RAW_BUCKET`, `R2_ENDPOINT`, `R2_BUCKET=s1d1-clean`,
  `GLINER_MODEL_DIRS` (the image default, spelled out), `GLINER_THRESHOLD=0.5`; `R2_ACCESS_KEY_ID` and
  `R2_SECRET_ACCESS_KEY` from Secret Manager. Only `eventarc-invoker` may invoke it: there is no `allUsers` binding.
- **Eventarc** `s1d1-raw-finalized`: `google.cloud.storage.object.v1.finalized` on the raw bucket, delivered to
  `POST /` of the service with an OIDC token of `eventarc-invoker`. The GCS service agent has
  `roles/pubsub.publisher` on the project (it publishes the bucket events).
- **Budget** (`budget.tf`): 5 EUR a month on this project, alerts at 50, 90 and 100 % of the current spend, emailed to
  the billing account administrators and users (Cloud Billing's default recipients). It alerts and never stops anything.
- **Workload Identity Federation** (`wif.tf`): pool `github`, OIDC provider `github-main` (issuer
  `https://token.actions.githubusercontent.com`). The condition is
  `assertion.repository == "<github_repository>" && assertion.ref == "refs/heads/main"`, plus the numeric owner id when
  `github_repository_owner_id` is set (GitHub advises it: a name can be released and re-registered by someone else).
  `ci-publisher` can be impersonated by `principalSet .../attribute.repository/<github_repository>` only.

### Ingress `INTERNAL_ONLY` and Eventarc

Cloud Run accepts Eventarc and Pub/Sub subscriptions under the "internal" ingress setting, on two conditions stated by
the documentation: the trigger is in the same project (or VPC Service Controls perimeter) as the service, and it calls
the default `run.app` URL, not a custom domain (https://docs.cloud.google.com/run/docs/securing/ingress). Both hold
here, so the service uses `INGRESS_TRAFFIC_INTERNAL_ONLY`. Consequences: `default_uri_disabled` must stay `false`
(Eventarc uses that URL), and a request from the internet is refused by Google's front end whatever its token.
This is checked against the documentation, not against a live project: the first apply is where it is proven. If the
trigger shows delivery failures with 403/404 in Cloud Run's logs, that is the first thing to look at; the fallback
is `INGRESS_TRAFFIC_ALL` with the same IAM (still no public invoker).

### Retries

`google_eventarc_trigger` accepts a `retry_policy` with `max_attempts = 1` only, and has no dead-letter field, so none is
set. Eventarc then uses its default: the Pub/Sub subscription it manages retries every non-2xx answer with a backoff
from 10 s to 600 s and keeps the message 24 h (https://docs.cloud.google.com/eventarc/docs/retry-events). The
anonymizer already answers 204 to every event that can never succeed (wrong name, wrong bucket, bad body), so only a
real failure (storage, configuration, a broken model) is retried. The cost of an object that fails for good is at most
about 150 deliveries in the 24 h, each one possibly loading the models: watch Cloud Run's error rate. To change the
policy or add a dead-letter topic, import the subscription (`terraform state show google_eventarc_trigger.raw_finalized`
gives its name) into a `google_pubsub_subscription` with `ignore_changes = [push_config]`; the documentation above
describes it.

The subscription's acknowledgement deadline is not configurable here either. If an object takes longer to clean than
that deadline, Pub/Sub may deliver it again while the first run goes on; the clean object is written under the same key,
so the second run replaces it and nothing is duplicated, only compute is spent.

## Token permissions (`cloudflare_api_token`)

Create the provider token in the dashboard with the permissions that the provider documents for each resource:

- Account: Workers Scripts Write, Workers R2 Storage Write, Cloudflare Tunnel Write (Connectors).
- User: **API Tokens Write** (the dashboard shows it as User, API Tokens, Edit). `cloudflare_api_token` creates a
  user-owned token (`/user/tokens`) and `cloudflare_api_token_permission_groups_list` reads
  `/user/tokens/permission_groups`; the provider documents "API Tokens Write" (or Read, for the list) for both. Cloudflare
  offers this permission only through the "Create additional tokens" template, not in the custom token builder: start
  the provider token from that template and add the other permissions to it. A token cannot grant more than its
  creator holds, so the provider token also needs the R2 write permission above.
- Zone `qoo.gy`: Workers Routes Write, DNS Write, and the zone ruleset (Zone WAF / rate limiting) write permission.

## Oracle node (`oci.tf`)

One `VM.Standard.A1.Flex` instance (2 OCPU, 12 GB, Always Free range) on the latest Canonical Ubuntu 24.04 aarch64
image, in a dedicated VCN (`10.0.0.0/24`) with a public subnet, an internet gateway and a route to it. The cloud-init
is `../node/cloud-init.yaml.tftpl`, filled with the tunnel token, the community token and the URL of `node/desired.env`
on `main`.

- **No inbound access at all.** The subnet's `security_list_ids` holds only `s1d1-node-no-ingress` (egress to
  `0.0.0.0/0`, every protocol, and no ingress rule), which replaces the VCN's default list that would allow SSH and
  ICMP in. The public IP is there only so the node can go out (tunnel, ghcr.io, raw.githubusercontent.com, apt).
  The compose publishes no port.
- **No SSH key.** To look inside the node, use the OCI console (Compute, the instance, Console connection) or Cloud
  Shell. The image sets no local password, so the serial console only shows boot and cloud-init output. A node you
  cannot repair is replaced (`terraform apply -replace=oci_core_instance.node`): it holds no state that matters (the
  capture folder is kept two days).
- **No drift on a running node.** `lifecycle { ignore_changes = [metadata, source_details] }`: a newer Ubuntu image or
  an edited cloud-init does not replace the instance. The node follows `desired.env` by itself.
- **"Out of host capacity".** A1 capacity is often exhausted in a region. The apply then fails on the instance only
  (the network is created). Set `oci_availability_domain_index` to another value (`1`, `2`; `eu-paris-1` has a single
  AD, in which case just retry later) and apply again.
- **API key.** Create it for a user in the OCI console (Profile, API keys, Add), put the fingerprint in
  `oci_fingerprint` and the PEM in the sensitive workspace variable `oci_private_key`. That user needs `manage`
  rights on `virtual-network-family` and `instance-family` in the compartment (the image is a platform image).

## Manual steps (Terraform cannot do them)

1. **Route failure mode (fail open / fail closed).** `cloudflare_workers_route` (5.28.1) has only `zone_id`,
   `pattern` and `script`. The setting exists only in the dashboard: Workers & Pages, the Worker, Settings, Domains
   & Routes, the route, "Failure mode". Fail open bypasses the Worker when it cannot run, so `/api/*` would go
   straight to the tunnel without the allow-list, the cache and the maintenance switch; fail closed returns a
   Cloudflare 1027 page. For `s1d1.qoo.gy/*` choose **fail open** (docs/26
   decision 22): it is safe because the tunnel ingress still lists only the API paths and answers 404 for the rest
   (`/metrics` included), and what is lost when the Worker cannot run is the cache, the maintenance switch and the
   landing, which is accepted. For `s1d1.qoo.gy/ingest` choose **fail closed**, so that ingestion never reaches a
   tunnel that does not serve it. Check both values after each apply (open question 4 of docs/26).
2. **Tunnel connector.** The tunnel exists after the apply, with no connector: the nodes start `cloudflared` with
   `terraform output -raw tunnel_token`.
3. **Google Cloud bootstrap, by hand, once.** A project with billing linked, `serviceusage.googleapis.com` and
   `cloudresourcemanager.googleapis.com` enabled (Terraform enables the other APIs but needs these two to do it), and a
   service account for HCP (`GOOGLE_CREDENTIALS` on the workspace, or dynamic provider credentials) with: Project IAM
   Admin, Service Usage Admin, Storage Admin (it covers the HMAC key), Service Account Admin,
   Cloud Run Admin, Eventarc Admin, Pub/Sub Admin, Secret Manager Admin, Artifact Registry Administrator, Workload
   Identity Pool Admin, `iam.serviceAccountUser` on the three runtime accounts, and Billing Account Costs Manager
   (or Billing Account Administrator) **on the billing account** for the budget. This account is the most powerful
   credential of the project: keep it out of the repository and rotate it.
4. **First image.** The image must exist in Artifact Registry before the Cloud Run service is created, so the first
   apply runs with `anonymizer_image` null (its default) and creates no anonymizer; the CI then pushes the image
   (`workflow_dispatch`), a pull request adds `terraform/images.auto.tfvars` with its digest, and the next apply
   creates the anonymizer. No `-target` is needed (the workspace is VCS-driven). Details: `docs/operations.md`,
   section 3.
5. **Currency.** `budget_currency` must equal the billing account's currency; the API rejects the budget otherwise.
6. **Account.** R2 must be enabled on the account before the first apply.

## Cloudflare behaviours that matter here

- **The Worker's `ORIGIN` is `https://s1d1.qoo.gy` and it does not loop.** Cloudflare: "Routes cannot be the target
  of a same-zone `fetch()` call", and `fetch()` from a route Worker goes to the application server defined in the
  zone's DNS (here the tunnel CNAME), see https://developers.cloudflare.com/workers/configuration/routing/routes/.
  This holds for **routes**; a Custom Domain on the same hostname would invoke the Worker again, so never add one.
- **Rate limit and same-zone subrequests.** Cloudflare may count a Worker's same-zone subrequest as a second
  request (https://developers.cloudflare.com/waf/rate-limiting-rules/troubleshooting). Its remedy adds
  `cf.worker.upstream_zone` to the expression; the Free plan restricts the rule's fields, so the expression is kept
  as specified. Watch the first days of traffic: if legitimate callers are blocked sooner than 100 requests per
  10 s, that is why.
- **Free plan.** One rate limit rule, period 10 s, mitigation 10 s, count per IP (and per data centre, `cf.colo.id`
  is implicit).
