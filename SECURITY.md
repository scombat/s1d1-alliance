# Security policy

## Supported versions

Only what is deployed from `main` receives security fixes. There are no releases to keep patched.

## Reporting a vulnerability

Please do not open a public issue. Report privately, by either of:

- GitHub private vulnerability reporting (*Security* tab, *Report a vulnerability*,
  https://github.com/scombat/s1d1-alliance/security/advisories/new);
- an email to `steve@qoo.gy`.

Give the path or file, what you did, what you saw and what you expected. Never send real personal data in a report: use
invented data. This is a solo-maintained project: we intend to acknowledge a report within 7 days and to tell you what
we plan to do about it; there is no bug bounty.

## Scope

In scope: the Workers (`workers/`), the ingestion path and its contract (`contract/`), anonymiser bypasses that let
personal data reach the clean corpus (`anonymizer/`), the Terraform and its IAM (`terraform/`), and the node update path
(`node/`).

Out of scope:

- Rate limits: the Workers rate limits are measured as not enforced at our scale (docs/operations.md, section 9, smoke tests 6 and 8); reports that only restate this are known.
- Volumetric denial of service.
- The platforms themselves (Cloudflare, Google Cloud, Hetzner Cloud): report those to their vendors.
- The behaviour of the routing model. That belongs to the model card on the Hugging Face Hub.
