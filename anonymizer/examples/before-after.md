# Before and after

Produced by `scripts/sample.py` with the two models of the image and the patterns. Every text is an invented
sentence from `tests/fixtures`, never a real prompt. Names and addresses come from the models; emails,
phones, IBANs, cards, IPs and secrets from the validated patterns. Where an email span and a
`[URL_CREDENTIALS]` span overlap, the longer one wins: the `user:password@host` of a URL is marked `[EMAIL]`,
and the credentials are masked either way.

## Covered scripts: masked

| before | after |
|---|---|
| `Hi, I am Margaret Thornbury, I live at 42 Willow Lane, Bristol BS1 4DJ, and my phone number is +44 117 496 0123.` | `Hi, I am [PERSON], I live at [ADDRESS], and my phone number is [PHONE].` |
| `Bonjour, je m'appelle Élodie Marchand, j'habite 18 rue des Tanneurs, 69002 Lyon, et mon numéro est le 06 71 23 45 89.` | `Bonjour, je m'appelle [PERSON], j'habite [ADDRESS], et mon numéro est le [PHONE].` |
| `Hallo, ich heiße Jürgen Wohlfahrt, wohne in der Lindenstraße 7, 10969 Berlin, und meine Nummer ist 030 2345 6789.` | `Hallo, ich heiße [PERSON], wohne in der [ADDRESS], und meine Nummer ist [PHONE].` |
| `Hola, me llamo Lucía Fernández Ortega, vivo en la calle Mayor 23, 28013 Madrid, y mi teléfono es +34 612 345 678.` | `Hola, me llamo [PERSON], vivo en la [ADDRESS], y mi teléfono es [PHONE].` |
| `Γεια σας, με λένε Γιώργο Παπαδόπουλο, μένω στην οδό Ερμού 25, Αθήνα, και το τηλέφωνό μου είναι +30 694 123 4567.` | `Γεια σας, με λένε [PERSON], μένω στην [ADDRESS], και το τηλέφωνό μου είναι [PHONE].` |
| `שלום, שמי דוד כהן, אני גר ברחוב הרצל 10, תל אביב, והטלפון שלי 052-123-4567.` | `שלום, שמי [PERSON], אני גר ברחוב [ADDRESS], והטלפון שלי [PHONE].` |
| `مرحبا، اسمي ليلى الحسيني، أسكن في 15 شارع الملك فهد، الرياض، ورقم هاتفي 0551234567.` | `مرحبا، اسمي [PERSON]، أسكن في [ADDRESS]، ورقم هاتفي [PHONE].` |
| `你好，我叫王晓明，住在北京市朝阳区建国路88号，电话是13912345678。` | `你好，我叫[PERSON]，住在[ADDRESS]，电话是[PHONE]。` |
| `안녕하세요, 저는 김민준입니다. 서울특별시 강남구 테헤란로 152에 살고 있고 전화번호는 010-1234-5678입니다.` | `안녕하세요, 저는 [PERSON]. [ADDRESS] 살고 있고 전화번호는 [PHONE].` |

## Patterns

| before | after |
|---|---|
| `Write to jane.doe+news@example.org before Friday.` | `Write to [EMAIL] before Friday.` |
| `Call me at +33 6 12 34 56 78 tomorrow.` | `Call me at [PHONE] tomorrow.` |
| `IBAN : FR76 3000 6000 0112 3456 7890 189` | `IBAN : [IBAN]` |
| `My card is 4111 1111 1111 1111, exp 12/27.` | `My card is [CARD], exp 12/27.` |
| `The server is at 203.0.113.42 now.` | `The server is at [IP] now.` |
| `Use postgres://admin:s3cretPass@db.example.com:5432/app` | `Use postgres://[EMAIL]:5432/app` |
| `Authorization: Basic dXNlcjpodW50ZXIy` | `Authorization: Basic [SECRET]` |
| `The order 3f2b8c1a-9d4e-4f6a-b2c7-8e1d0a5f6b3c failed.` | `The order [ID] failed.` |

## Uncovered scripts: the whole prompt is dropped

A single letter in a script that the fixtures show the models miss (here Cyrillic, Japanese and Devanagari)
drops the prompt: it is not written to the clean corpus at all.

| before | after |
|---|---|
| `Здравствуйте, меня зовут Ольга Смирнова, я живу по адресу ул. Ленина, д. 12, кв. 5, Казань, мой телефон +7 917 234-56-78.` | **dropped**, not stored (a script the anonymiser does not cover) |
| `こんにちは、山田花子です。東京都新宿区西新宿2丁目8番1号に住んでいます。電話は090-1234-5678です。` | **dropped**, not stored (a script the anonymiser does not cover) |
| `नमस्ते, मेरा नाम राहुल शर्मा है, मैं 12 एमजी रोड, बेंगलुरु में रहता हूँ और मेरा फ़ोन नंबर +91 98765 43210 है।` | **dropped**, not stored (a script the anonymiser does not cover) |
| `Please translate this address for me: Ольга Смирнова, ул. Ленина, д. 12` | **dropped**, not stored (a script the anonymiser does not cover) |
