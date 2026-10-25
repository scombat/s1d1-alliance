# anonymizer

The Cloud Run Job that turns the raw captures into the clean corpus. Cloud Scheduler starts it every 2 days
(`terraform/gcp.tf`); it reads the raw objects `raw/YYYY/MM/DD/<uuid>.ndjson` that the ingest Worker wrote to the raw
Google Cloud Storage bucket, draws at most `MAX_PROMPTS` of their lines at random, keeps the decision-11 keys of each
drawn line, anonymises the prompt, and writes `clean/YYYY/MM/DD/<same uuid>.ndjson` to the R2 bucket `s1d1-clean`.
What it misses stays in the corpus for good, so it is built for recall on personal data and secrets, it fails closed
on what it cannot vouch for, and it is checked for leaving clean text alone.

Why a job and a draw (docs/31 of the project): the cost is then bounded per run, at most `MAX_PROMPTS` lines and one
hour of 2 vCPU, whatever the traffic. Until 2026-10-25 an Eventarc trigger called it, as a Cloud Run service, on every
raw object; that handler is still in `src/anonymizer/app.py` for a return to that mode, but nothing deploys it.

The contract it reads is `contract/ingest-v1.md`; `contract/fixtures/valid.ndjson` gives
`contract/fixtures/clean-expected.ndjson`.

## What it does

`python -m anonymizer.batch` (`src/anonymizer/batch.py`, the image's command):

1. Lists the objects under `raw/` of `RAW_BUCKET` (`google-cloud-storage`, with the identity of the job) and keeps the
   names `raw/YYYY/MM/DD/<uuid>.ndjson`. An object whose clean object already exists in R2 (HeadObject) is left out,
   so a run never cleans an object twice, and a run started by hand right after another cleans nothing again.
   **Draws** at most `MAX_PROMPTS` lines over all the other objects' lines, uniformly (a reservoir: one draw over all
   lines, not per object, and memory holds `MAX_PROMPTS` lines however much was sent). A line not drawn is never
   stored: its raw object is deleted by the bucket's 3-day lifecycle rule. An object with no line drawn has no clean
   object, so a later run may still draw from it while it exists.
2. Cleans the drawn lines in a random order (so that a deadline cuts a random part). Each line goes through
   `src/anonymizer/clean.py`. The prompt is first put in its canonical form (see "The canonical form" below):
   that form is the one checked, anonymised and stored. A line is dropped, and counted, when it is not JSON, has no
   prompt or no string `id`, has a `source` other than `capture:community`, `capture:direct` or
   `capture:selfhosted:<lower-case uuid>`, has a `terms_version` other than `v` and 1 to 3 digits, has a canonical
   prompt that is empty or longer than 65 536 characters, or has a single letter in a script the anonymiser does not
   cover, small capitals included (see below; counted as `dropped_uncovered_script`). Otherwise the clean line has exactly these keys, in this
   order:

   | key | value |
   |---|---|
   | `id` | the id when it is a UUID (lower-cased), otherwise the first 32 hex of its SHA-256 |
   | `time` | the capture day `YYYY-MM-DD` (a real calendar date); without one in `time`, the day the Worker received the line; else null |
   | `prompt` | the canonical form, anonymised |
   | `source` | as checked above (it keeps the instance UUID of a self-hosted node, decision 13) |
   | `model_revision` | 7 to 40 lower-case hex, else null |
   | `terms_version` | as checked above |
   | `format` | `systemone`, `decisions`, `chat`, `classify` or `predict`, else null |
   | `pred_tier` | a tier name tantive emits (native `TRIVIAL` `LOW` `MEDIUM` `HIGH` `EXPERT`, LiteLLM `SIMPLE` `MEDIUM` `COMPLEX` `REASONING`), else null |
   | `pred_task` | one of tantive's 19 task names, else null |
   | `pred_score`, `pred_needs_tools`, `pred_confidence` | a finite number in [0, 1] (not a boolean), as a float, else null |

   Never `instance_id`, `received`, `variant`, `v`, an IP address or a header.
3. A line that raises while it is cleaned is dropped and counted as `failed`; when every cleaned line raises (a broken
   model), the run fails and writes nothing.
4. **Stops cleaning at `MAX_SECONDS`** (3 300 s in production, under the job's 3 600 s timeout) and goes on to the
   writes with what it kept, so a slow model bounds the run instead of making it longer.
5. Writes, for each raw object with at least one kept line, its clean lines as one object to R2 (`boto3`, S3 API)
   under the same date and UUID.
6. Exits 0, or 1 when the run could not go through (storage, every line failing, a bad setting): the execution then
   shows as failed in Cloud Run, and the next run, at most 2 days later, takes the objects up again.

It never logs a prompt, a line, an object name or an error message: the last log line (JSON on stderr, for Cloud
Logging), `batch done`, carries counts only (`objects`, `already_clean`, `lines`, `drawn`, `cleaned`, `kept`,
`dropped`, `dropped_uncovered_script`, `failed`, `written`, `deadline_reached`, `seconds`); a failure logs the class
name of the error.

## The canonical form

Look-alike characters would carry a value past the patterns and the models: `+33 𝟔 𝟏𝟐 𝟑𝟒 𝟓𝟔 𝟕𝟖` is a phone number
in mathematical digits, `ᴶᵒʰⁿ ˢᵐⁱᵗʰ` a name in modifier letters, and a zero-width space inside an email or an `sk-`
key cuts it in two. So `src/anonymizer/canonical.py` folds the prompt before anything reads it, and the clean line
stores the folded text (it is not the text that was sent, byte for byte):

1. NFKC: mathematical, full-width, super- and subscript, modifier and circled letters and digits become the plain
   ones, ligatures their letters (`𝟔` is `6`, `ᴶᵒʰⁿ` is `John`, `ﬁ` is `fi`, `²` is `2`, `µ` is the Greek `μ`).
2. The invisible characters that split a word are removed: the soft hyphen (U+00AD), the zero-width space (U+200B),
   the word joiner (U+2060), the byte order mark (U+FEFF); the zero-width joiner (U+200D) except between two emoji
   (👨‍👩‍👧, 🏳️‍🌈 are kept); the zero-width non-joiner (U+200C) except between two letters of the Arabic script, where
   Persian writes with it (`می‌خواهم` is kept).

NFKC leaves small capitals as they are (`ᴊᴏʜɴ sᴍɪᴛʜ`, also the IPA `ɪ ʀ ʏ`), and two modifier capitals (`ᴯ ᴻ`): a
prompt that still holds one after NFKC is dropped as an uncovered script (no fixture shows the models read them). A
canonical prompt longer than the contract's 65 536 characters is dropped (NFKC can lengthen a text: `ﷺ` gives 18
characters). `tests/fixtures/lookalikes.txt` holds these cases.

## Markers

`[PERSON]`, `[EMAIL]`, `[PHONE]`, `[ADDRESS]`, `[ID]`, `[CARD]`, `[IBAN]`, `[SECRET]`, `[IP]`, `[URL_CREDENTIALS]`
(in English, neutral for a multilingual corpus).

`anonymize()` takes the union of the spans of the patterns and of the models. Overlapping spans become one span, which
gets the marker of the longest; a span is never dropped. The replacements are made from right to left.

## The models: an ensemble

`src/anonymizer/detector.py` runs two GLiNER models, each pinned to a commit of the Hub, and unites their spans:

| directory | model | commit | licence | why |
|---|---|---|---|---|
| `knowledgator` | `knowledgator/gliner-pii-base-v1.0` | `61726e0ad791dcab3e29339bbec3ad42ded65641` | Apache-2.0 | best English F1 |
| `urchade` | `urchade/gliner_multi_pii-v1` | `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d` | Apache-2.0 | multilingual encoder (mDeBERTa-v3): Arabic, Persian, Chinese, Hebrew and Korean names |

urchade's repository has no tokenizer; GLiNER takes it from `microsoft/mdeberta-v3-base` (MIT), whose commit
`a0484667b22365f84929a935b5e50a51f71f159d` `scripts/fetch_models.py` checks before it saves the tokenizer into the
model directory. That script is the only step that downloads: `uv run --extra model python scripts/fetch_models.py
/model` gives `/model/knowledgator` and `/model/urchade`, each loadable offline (the job loads them with
`local_files_only=True`).

Both run at threshold 0.5 with the labels `person`, `address`, `phone number`, `email`, and `passport number`,
`national id number`, `social security number`, `tax id` (all `[ID]`). A `person` span that is a single letter, or a
single token written in lower case that is a pronoun or a role of the conversation (`you`, `eu`, `你`, `user`,
`assistant`...), is not kept: they name no one, and urchade's model tags them often. The list holds no word that is also
a common given name (no `ben`, `ella`, `elle`, `ty`, `lei`), and a capitalised `Ella` or `Ben` is never suppressed (on 300 prompts of the router's test set, before the list was narrowed: 173 of its 610 person spans, in 25 prompts
that had no other).
What is left is mostly real names, of people and of fictional characters: on those 300 prompts, knowledgator's model
marks a person in 79 and urchade's in 117.

The encoders read at most 512 sub-word tokens: a long prompt is cut into windows of at most 384 tokens (counted with
each model's tokenizer, leaving room for the labels; the longest window measured is 410 tokens with them) with 32 words
in common between neighbours, and the offsets are mapped back to the prompt. Han and kana characters are given to the
models one by one, since their word splitter cuts on spaces and punctuation only.

## Scripts: covered, or dropped

A script is **covered** when, in the slow tests, the ensemble masks every name and every address of every fixture of
`tests/fixtures/pii/` written in it (each fixture is one invented sentence with a name, an address and a phone
number). A prompt is **dropped**, not stored (`src/anonymizer/coverage.py`), as soon as it holds a single letter in a
script that is not covered: the models miss names in those scripts, and a name can be two letters long (`Ли Хо`), so no
count or share of such letters would be safe. `Translate привет into English`, `Олег lives at 12 Baker Street.` and a
long English paragraph ending in `My colleague Иван will call.` are all dropped. The rule reads the canonical form,
in which NFKC has already turned ordinal indicators (`nº`, `1ª`), letterlike symbols (`ℝ`), the micro sign and most
super- and subscript and modifier letters into plain ones; the letters left that belong to no script and hold no name
are not counted (modifier letters such as the IPA `ˈ ː`). Small capitals are counted as a script of their
own, not covered (`Please write to ᴊᴏʜɴ sᴍɪᴛʜ` is dropped). `tests/fixtures/mixed_scripts.txt` holds these cases.

| script | fixtures | result at the pinned commits | |
|---|---|---|---|
| Latin | en, fr, de, es, it, pt, pl, tr, vi | all names and addresses masked | covered |
| Greek | el | masked | covered |
| Hebrew | he | masked | covered |
| Arabic | ar, fa | masked | covered |
| Han (Chinese) | zh | masked | covered |
| Hangul | ko | masked | covered |
| Cyrillic | ru, uk | uk masked; ru: the address `ул. Ленина, д. 12, кв. 5, Казань` survives | dropped |
| Japanese (Han with kana) | ja | the name is masked, the address `東京都新宿区西新宿2丁目8番1号` survives | dropped |
| Devanagari | hi | nothing masked by the models | dropped |
| Thai | th | nothing masked by the models | dropped |
| any other | none | never tested | dropped |

Han characters count as Japanese (dropped) when kana are 5 % of the CJK letters (kana and Han, so Latin padding does
not count), and as Chinese (covered) otherwise. On the router's training set (98 033 prompts of six public sources)
this rule drops 10.84 % of the prompts (10 628): Cyrillic 5 761, Japanese 991, then Devanagari, Sinhala, Gurmukhi,
Tamil, Telugu, Gujarati, Thai and others; 796 of them are mostly in covered scripts, with a few uncovered letters.
A script becomes covered when a fixture shows it: add the fixture, see the slow tests pass, add the script to
`COVERED_SCRIPTS`; `test_uncovered_scripts_are_really_missed` fails the day an uncovered script is in fact masked.

## The patterns

`src/anonymizer/patterns.py`, each one validated; all of them run, and overlapping findings are united:

| marker | what | check |
|---|---|---|
| `[EMAIL]` | addresses with any Unicode letter or digit in the local part and the domain (`marie.lefèvre@exemple.fr`, `иван@почта.рф`, `王伟@qq.com`) | an ASCII TLD, a punycode one or a non-ASCII one; not `logo@2x.png`, not `git@github.com:`, not the `password@host` of a URL |
| `[PHONE]` | E.164 (`+33 6 12 34 56 78`), `00` international, national with a trunk `0`, US `(415) 555-0132` / `415-555-0132`, Italian mobiles `333 123 4567`, Chinese mobiles, Russian `8 (912) …` | digit counts and group shapes per form; one kind of separator (a date and a time mix them); not binary, not a rising lottery draw, not a part cut out of a longer number |
| `[IBAN]` | registered countries, with or without spaces | the registry length of the country, ISO 13616 mod 97 |
| `[CARD]` | 13 to 19 digits starting with 2 to 6, alone or grouped 4-4-4-4 (or 4-6-5, 4-6-4) | Luhn |
| `[IP]` | IPv4 and IPv6 (with a zone) | `ipaddress`; not loopback, unspecified, netmasks, networks (`x.x.x.0`), section numbers (`5.1.2.1`), product versions (`Chrome/110.0.0.0`) |
| `[URL_CREDENTIALS]` | the `user:password` of `scheme://user:password@host` | |
| `[ID]` | UUIDs, `0x` + 40 hex (account addresses), French NIR (with its key), US SSN (dashed) | |
| `[SECRET]` | known prefixes (`sk-`, `ghp_` and the other GitHub prefixes, `github_pat_`, `AKIA`/`ASIA`, `xox[abprs]-`, `hf_`, `AIza`, `glpat-`, `npm_`, Stripe keys, JWTs), PEM blocks (`-----BEGIN` through its `-----END` line, or to the end of the text when there is none), `Bearer`/`Basic` credentials, values given to a secret, token or key (`api_key = "…"`, `DB_PASSWORD=…`), any value given to a password (`password is sunshine`, `mot de passe : soleil`, `пароль: …`, letters only included), runs of 32+ hex digits mixing letters and digits, and random tokens | see below |

The patterns read the canonical form (above); on it, Arabic-Indic, Persian and Devanagari digits, no-break spaces and
Unicode dashes are also read as their ASCII forms (one character for one, so offsets do not move).

### Random tokens: the trade-off

A run of 32 or more of `A-Z a-z 0-9 + / = _ -` that has letters and digits and a Shannon entropy of 4.0 bits per
character or more is a `[SECRET]`; so is a run of 32+ hex digits that mixes letters and digits (hex is at most 4 bits
per character, and a hex key cannot be told from a hash, so git SHAs and SHA-256 sums go too). Except:

- a UUID: it is an `[ID]`;
- an identifier: when 60 % of its letters are in pronounceable chunks of 4 letters or more (camelCase, snake_case,
  file names, chemical names). Random strings have long runs of consonants: 0.2 % of random tokens pass as identifiers
  in a test of 3 000;
- base64 of plain text (it decodes to printable UTF-8 with a space), unless the decoded text itself holds something the
  patterns find;
- a URL path or query string (a part is a lower-case word, the token follows a `.` or starts with `/`, or a `=` is
  inside it): only the parts of 20 characters or more that are random (mixed case, a digit, not an identifier; or
  32+ hex) are replaced, so `/file/d/<id>/view` keeps its words and loses its identifier. The cost: base64 that
  happens to contain a short lower-case part between two `/` keeps its short parts (3 of the 600 random tokens of the
  tests, all base64 with `/`).

On the 98 033 prompts of the router's training set, before the round that made emails Unicode, hex runs and
letters-only passwords secrets, the patterns changed 0.62 % of the prompts; read by hand, the replaced spans were mostly
real emails, phones, server addresses, identifiers and keys, and the false positives left were mostly reference
numbers taken for phones and public identifiers (video, document and account ids) taken for secrets. The patterns take
0.17 ms per prompt (mean, 618 characters).

## Environment

| name | default | role |
|---|---|---|
| `RAW_BUCKET` | (required) | the only bucket whose objects are read |
| `MAX_PROMPTS` | `5000` | most lines drawn and cleaned by one run (Terraform: `anonymizer_max_prompts`) |
| `MAX_SECONDS` | `3300` | the run stops cleaning after this many seconds and writes what it kept |
| `R2_ENDPOINT` | | `https://<account>.r2.cloudflarestorage.com` (for a bucket in the EU jurisdiction: `https://<account>.eu.r2.cloudflarestorage.com`) |
| `R2_BUCKET` | `s1d1-clean` | the clean bucket |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | | an R2 API token limited to that bucket (secrets, never in this repository) |
| `GLINER_MODEL_DIRS` | `/model/knowledgator,/model/urchade` | the model directories, comma-separated (one model each) |
| `GLINER_THRESHOLD` | `0.5` | the models' threshold |

The raw bucket is read and listed with the job's service account (no key). The models are loaded once per run, after
the draw, and only when a line was drawn.

## Dependencies

Each one is in `pyproject.toml` and pinned in `uv.lock`.

- `google-cloud-storage`: lists and reads the raw bucket with the identity of the job (no key to store or rotate).
- `boto3`: checks and writes R2 objects through its S3 API (request signing is not something to write by hand).
- `uvicorn`: serves the Eventarc handler of `app.py`, kept for a return to one run per object (not deployed). The
  application itself is a plain ASGI callable, no web framework.
- The `model` extra, installed by the image, the slow tests and `scripts/fetch_models.py`, not by CI's fast tests:
  - `gliner`: loads and runs the GLiNER models; there is no other maintained implementation of its span decoding;
  - `torch`: needed by `gliner`; listed only so that `uv` takes it from the PyTorch CPU index (`[tool.uv.sources]`):
    the job runs on CPU, and the PyPI Linux wheel would pull gigabytes of CUDA libraries.
- `pytest` (dev group only): the tests.

`gliner`, `torch`, `google-cloud-storage` and `boto3` are imported inside the adapters only, so the fast tests need
neither the models nor the network.

## Tests

From `anonymizer/`, on Python 3.12:

```
uv run --python 3.12 pytest -q -m "not slow"           # what CI runs: patterns, cleaning, handler, adapter with a fake model
uv run --python 3.12 --extra model pytest -q -m slow   # the two real models, from the Hub at the pinned commits
```

Set `GLINER_MODEL_DIRS` to run the slow tests on directories made by `scripts/fetch_models.py` (they pass offline,
with an empty Hugging Face cache). Measured on an Apple M-series CPU, the ensemble takes 0.073 s per fixture prompt
(mean of 20), 0.37 s for a 2 000-character prompt, 3.2 s for 20 000 and 10.5 s for 65 536 (Cloud Run's vCPU will be
slower). All the examples in the tests are invented; the secrets are assembled at run time so that gitleaks sees no
key-shaped literal.
