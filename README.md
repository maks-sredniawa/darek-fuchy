# darek-fuchy

Fuchy for [Darek](https://github.com/maks-sredniawa/darek), the Polish voice assistant for Android.
A **fucha** is a small data file of voice Commands (*„Darek, jaka pogoda?"*) that Darek installs from
his fuchy page. It is data, never code: each Command picks one of the actions built into Darek. Why it
works this way is [ADR-0015](https://github.com/maks-sredniawa/darek/blob/main/docs/adr/0015-darek-takes-on-fuchy.md).

Every fucha here is checked in CI the way Darek checks one at install, then signed by MaksForge
after it reaches `main`. Darek shows a signed fucha with the MaksForge badge; any other `.darek`
file still installs, after a warning that MaksForge has not reviewed it.

## Layout

| Path | What it is |
| --- | --- |
| `fuchy/<id>/fucha.json` | A fucha's source, the file to edit. The folder is named after its `id`. |
| `docs/fuchy/<id>.darek` | The signed file Darek installs, written by CI. Never edit it by hand. |
| `docs/fuchy.json` | The list Darek's fuchy page reads, written by CI. |
| `scripts/check.py` | The install checks, the clash rules and the vocabulary check. |
| `scripts/sign.py` | Signs each fucha and writes `docs/`. |
| `keys/fuchy-public.pem` | The public half of the signing key. |

`docs/` is published at <https://maksforge.com/darek-fuchy/>, so a fucha's file is at
`https://maksforge.com/darek-fuchy/fuchy/<id>.darek` and the list at
`https://maksforge.com/darek-fuchy/fuchy.json`.

## The format

`fucha.json` is format 1, defined in Darek's
[phase-F.md, section 2](https://github.com/maks-sredniawa/darek/blob/main/docs/phase-F.md#2-fuchy-as-data).
The weather fucha, [`fuchy/pogoda/fucha.json`](fuchy/pogoda/fucha.json), is a complete example:

```json
{
  "format": 1,
  "id": "pogoda",
  "version": 1,
  "language": "pl",
  "name": "Pogoda",
  "description": "Darek mówi, jaka jest pogoda w twoim mieście. Dane pogodowe: Open-Meteo.com (CC BY 4.0).",
  "author": "MaksForge",
  "profanity": false,
  "permissions": ["internet"],
  "addresses": ["https://api.open-meteo.com/v1/forecast", "https://geocoding-api.open-meteo.com/v1/search"],
  "sends": ["chosen_city"],
  "commands": [
    { "id": "jaka-pogoda", "phrase": "jaka pogoda", "action": "weather", "decoys": ["jaka przygoda"] }
  ]
}
```

A Command's `phrase` is written without the name: Darek puts *Darek* or *Daria* in front, whichever
Persona the user chose. `reply` (and `reply_feminine`, for Daria) is what Darek says for the `speak`
action; `weather` builds its own answer. Raise `version` on every change: it is how Darek tells an
update from the fucha he already has.

The values Darek knows today:

| List | Values |
| --- | --- |
| `action` | `speak` (needs a `reply`), `weather` (needs `internet`). `call` and `update_fuchy` are Darek's own and refused here. |
| `permissions` | `internet`; `call` is Darek's own |
| `sends` | `chosen_city`, the city the user chose when switching the fucha on |

## How a fucha is checked

`scripts/check.py` runs on every pull request and every push to `main`. A fucha passes only if:

- `format` is 1, `language` is `pl`, every key is known and every value has the right type;
- `id` matches `[a-z0-9][a-z0-9-]{0,63}`, is not `darek` (Darek's own) and equals its folder's name;
- every `action`, `permission` and `sends` value is on Darek's lists, and every action's permission
  is declared;
- every address is HTTPS to one exact host, with no user, port, query or fragment;
- every `speak` Command has a non-blank `reply`, and every phrase has words;
- no phrase holds a cancel word (*stop*, *anuluj*);
- every word of every phrase and Decoy is in the recogniser's model, `vosk-model-small-pl-0.22`, as
  Vosk silently drops a word its model does not know;
- no phrase clashes with Darek's own Commands or with another fucha here: none identical to, or
  holding the words of, another; none within two letters of another over the whole phrase; and no
  word within two letters of *darek*, *daria* or another Command's word.

Darek runs the same checks again at install, except the vocabulary check, which needs the model on
the computer. To run them locally, with the model unzipped somewhere:

```bash
python3 -m unittest discover -s scripts
pip install vosk==0.3.45
python3 scripts/check.py --model path/to/vosk-model-small-pl-0.22
```

## How a fucha is signed

On every push to `main` that changes a fucha, `.github/workflows/sign.yml` runs the checks again,
then signs each fucha whose file changed with ECDSA P-256 over SHA-256 (`openssl dgst -sha256 -sign`,
a DER signature) over the **exact bytes** of its `fucha.json`. No JSON is re-serialised, so the
signature can be checked without canonicalising anything. The signed file is a JSON envelope:

```json
{ "fucha": "<base64 of fucha.json's bytes>", "signature": "<base64 of the DER signature>" }
```

The workflow verifies every signature against the public key before it commits `docs/` as
`chore: sign fuchy`. The private key lives only in this repo's `FUCHY_SIGNING_KEY` secret. It runs
only on `main`, after a merge, never on a pull request's code.

To verify a published fucha yourself:

```bash
curl -fsSO https://maksforge.com/darek-fuchy/fuchy/pogoda.darek
python3 -c "import json,base64; e=json.load(open('pogoda.darek')); open('fucha.json','wb').write(base64.b64decode(e['fucha'])); open('fucha.sig','wb').write(base64.b64decode(e['signature']))"
openssl dgst -sha256 -verify keys/fuchy-public.pem -signature fucha.sig fucha.json
```

## The public key

SPKI, DER in base64, as Darek ships it:

```text
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEylQl66lypEahQLgByRLtYBLgvlPKdM1HAFkt1UAlUaX+7l1tSsc2opALHYZ1KiHvKGhyECB2qEJY12BUXtRRdw==
```

PEM, the same as [`keys/fuchy-public.pem`](keys/fuchy-public.pem):

```text
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEylQl66lypEahQLgByRLtYBLgvlPK
dM1HAFkt1UAlUaX+7l1tSsc2opALHYZ1KiHvKGhyECB2qEJY12BUXtRRdw==
-----END PUBLIC KEY-----
```

## Contributing

For now this repo holds MaksForge's own fuchy, and pull requests from others are not accepted yet.

## Licence

All rights reserved, source available: see [`LICENSE`](LICENSE).
