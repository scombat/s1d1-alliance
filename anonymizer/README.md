# anonymizer

The Cloud Run service that turns the raw captures into the clean corpus. Eventarc calls it when the ingest Worker
writes an object `raw/YYYY/MM/DD/<uuid>.ndjson` to the raw Google Cloud Storage bucket; it reads the object, keeps the
decision-11 keys of each line, anonymises the prompt, and writes `clean/YYYY/MM/DD/<same uuid>.ndjson` to the R2
bucket `s1d1-clean`. What it misses stays in the corpus for good, so it is built for recall on personal data and
secrets, and checked for leaving clean text alone.

The contract it reads is `contract/ingest-v1.md`; `contract/fixtures/valid.ndjson` gives
`contract/fixtures/clean-expected.ndjson`.

## What it does

1. `POST /` with the CloudEvent of Eventarc in binary mode: the body is the storage object (`bucket`, `name`), the
   `ce-type` header is `google.cloud.storage.object.v1.finalized`. Another event type, or an object whose name is not
   `raw/YYYY/MM/DD/<uuid>.ndjson`, is acknowledged with 204 and left alone. A body that is not a storage object is a
   400. Another path is a 404, another method a 405.
2. Reads the object (`google-cloud-storage`, with the identity of the service), splits it into lines, and cleans each
   line (`src/anonymizer/clean.py`):
   - keeps `id`, `time`, `prompt`, `source`, `model_revision`, `terms_version`, `format`, `pred_tier`, `pred_score`,
     `pred_task`, `pred_needs_tools`, `pred_confidence`, in that order, and nothing else (no `instance_id`, no
     `received`, no `variant`, no `v`);
   - cuts `time` to the day (`YYYY-MM-DD`); a line without a usable `time` takes the day the Worker received it
     (`received`);
   - replaces every span found in the prompt by its marker.

   A line that is not JSON or has no prompt is dropped and counted.
3. Writes the clean lines as one object to R2 (`boto3`, S3 API) under the same date and UUID. A rewrite replaces the
   object, so an event delivered twice gives one clean object. An object with no good line writes nothing.
4. Answers 204. Any failure (read, model, write) answers 500, and Eventarc delivers the event again.

It never logs a prompt, a line or an error message: the log lines (JSON on stderr, for Cloud Logging) carry the object
name, the counts of kept and dropped lines, and the class name of an error.

## Markers

`[PERSON]`, `[EMAIL]`, `[PHONE]`, `[ADDRESS]`, `[ID]`, `[CARD]`, `[IBAN]`, `[SECRET]`, `[IP]`, `[URL_CREDENTIALS]`
(in English, neutral for a multilingual corpus).

`anonymize()` takes the union of the spans of the patterns and of the model. Overlapping spans become one span, which
gets the marker of the longest; the replacements are made from right to left.

## The two detectors

**The model** (`src/anonymizer/detector.py`): `knowledgator/gliner-pii-base-v1.0`, threshold 0.5, with the labels
`person`, `address`, `phone number`, `email`, and `passport number`, `national id number`, `social security number`,
`tax id` (all `[ID]`). The encoder reads at most 512 sub-word tokens: a long prompt is cut into windows of at most 384
tokens (counted with the model's own tokenizer, which leaves room for the labels; the longest window measured is 410
tokens with them) with 32 words in common between neighbours, and the offsets are mapped back to the prompt. Han and
kana characters are given to the model one by one, since its word splitter cuts on spaces and punctuation only.

**Known limit of the model:** its encoder is the English DeBERTa-v3 (the model card says `language: en`). On the
fixtures of `tests/fixtures/pii/`, it finds the names in English, French, German, Spanish and Russian, but not the
Chinese name (score under 0.35) nor the Arabic one (0.44, under the threshold), and none of the Russian, Chinese and
Arabic addresses. Emails, phones and identity numbers in those languages are still caught by the patterns. The slow
test records the two missed names as strict expected failures.

**The patterns** (`src/anonymizer/patterns.py`), each one validated:

| marker | what | check |
|---|---|---|
| `[EMAIL]` | addresses with an ASCII local part and a real TLD | not `logo@2x.png`, not `git@github.com:` |
| `[PHONE]` | E.164 (`+33 6 12 34 56 78`), `00` international, national with a trunk `0`, US `(415) 555-0132` / `415-555-0132`, Chinese mobiles, Russian `8 (912) …` | digit counts and group shapes per form; one kind of separator (a date and a time mix them); not binary, not a rising lottery draw, not a part cut out of a longer number |
| `[IBAN]` | registered countries, with or without spaces | the registry length of the country, ISO 13616 mod 97 |
| `[CARD]` | 13 to 19 digits starting with 2 to 6, alone or grouped 4-4-4-4 (or 4-6-5, 4-6-4) | Luhn |
| `[IP]` | IPv4 and IPv6 (with a zone) | `ipaddress`; not loopback, unspecified, netmasks, networks (`x.x.x.0`), section numbers (`5.1.2.1`), product versions (`Chrome/110.0.0.0`) |
| `[URL_CREDENTIALS]` | the `user:password` of `scheme://user:password@host` | |
| `[ID]` | UUIDs, `0x` + 40 hex (account addresses), French NIR (with its key), US SSN (dashed) | |
| `[SECRET]` | known prefixes (`sk-`, `ghp_` and the other GitHub prefixes, `github_pat_`, `AKIA`/`ASIA`, `xox[abprs]-`, `hf_`, `AIza`, `glpat-`, `npm_`, Stripe keys, JWTs, `-----BEGIN … -----END` blocks), `Bearer`/`Basic` credentials, values given to a password, secret, token or key (`password = "…"`, `DB_PASSWORD=…`, `mon mot de passe est …`), and random tokens | see below |

Arabic-Indic, Persian, Devanagari and full-width digits, no-break spaces and Unicode dashes are read as their ASCII
forms (one character for one, so offsets do not move).

### Random tokens: the trade-off

A run of 32 or more of `A-Z a-z 0-9 + / = _ -` that has letters and digits and a Shannon entropy of 4.0 bits per
character or more is a `[SECRET]`, except:

- a UUID (it is an `[ID]`) and pure hexadecimal (git SHAs, SHA-256 sums: at most 4 bits per character, so a
  hexadecimal secret is caught only by its context, `api_key = "…"`, or not at all);
- an identifier: when 60 % of its letters are in pronounceable chunks of 4 letters or more (camelCase, snake_case,
  file names, chemical names). Random strings have long runs of consonants: 0.2 % of random tokens pass as identifiers
  in a test of 3 000;
- base64 of plain text (it decodes to printable UTF-8 with a space), unless the decoded text itself holds something the
  patterns find;
- a URL path or query string (a part is a lower-case word, the token follows a `.` or starts with `/`, or a `=` is
  inside it): only the parts of 20 characters or more that are random (mixed case, a digit, not an identifier) are
  replaced, so `/file/d/<id>/view` keeps its words and loses its identifier. The cost: base64 that happens to contain
  a short lower-case part between two `/` keeps its short parts (3 of the 600 random tokens of the tests, all base64
  with `/`).

On the 98 033 prompts of the router's training set (six public sources), the patterns change 0.62 % of the prompts;
read by hand, the replaced spans are mostly real emails, phones, server addresses, identifiers and keys, and the false
positives left are mostly reference numbers taken for phones and public identifiers (video, document and account ids)
taken for secrets. The patterns take 0.17 ms per prompt (mean, 618 characters).

## Environment

| name | default | role |
|---|---|---|
| `R2_ENDPOINT` | | `https://<account>.r2.cloudflarestorage.com` (for a bucket in the EU jurisdiction: `https://<account>.eu.r2.cloudflarestorage.com`) |
| `R2_BUCKET` | `s1d1-clean` | the clean bucket |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | | an R2 API token limited to that bucket (secrets, never in this repository) |
| `GLINER_MODEL_DIR` | `/model` | the model directory (copied into the image at a pinned revision) |
| `GLINER_THRESHOLD` | `0.5` | the model's threshold |

The raw bucket is read with the service account of Cloud Run (no key). The model, the GCS client and the R2 client are
built on the first event that needs them.

## Dependencies

Each one is in `pyproject.toml` and pinned in `uv.lock`.

- `gliner`: loads and runs the GLiNER model; there is no other maintained implementation of its span decoding.
- `torch`: needed by `gliner`; listed only so that `uv` takes it from the PyTorch CPU index (`[tool.uv.sources]`): the
  service runs on CPU, and the PyPI Linux wheel would pull gigabytes of CUDA libraries.
- `google-cloud-storage`: reads the raw bucket with the identity of the service (no key to store or rotate).
- `boto3`: writes to R2 through its S3 API (request signing is not something to write by hand).
- `uvicorn`: serves the ASGI application. The application itself is a plain ASGI callable, no web framework.
- `pytest` (dev group only): the tests.

`gliner`, `torch`, `google-cloud-storage` and `boto3` are imported inside the adapters only, so the fast tests need
neither the model nor the network.

## Tests

From `anonymizer/`, on Python 3.12:

```
uv run --python 3.12 pytest -q -m "not slow"   # what CI runs: patterns, cleaning, the handler, the adapter with a fake model
uv run --python 3.12 pytest -q -m slow         # the real model, downloaded from the Hugging Face Hub at the pinned revision
```

The slow tests read `tests/fixtures/pii/*.txt` (one invented sentence per language, with a name, an address and a phone
number) and check that no name survives; set `GLINER_MODEL_DIR` to use a local copy instead of the Hub. Measured on an
Apple M-series CPU: 0.025 s per fixture prompt, 1.2 s for a 20 000-character prompt and 3.9 s for 65 536 characters
(Cloud Run's vCPU will be slower). All the examples in the tests are invented; the secrets are assembled at run time
so that gitleaks sees no key-shaped literal.
