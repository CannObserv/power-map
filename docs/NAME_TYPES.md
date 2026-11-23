# power-map — Name types reference

Annex of [NAMES.md](NAMES.md): one entry per person and organization name type
(#857) — what it means, when to use it, and how power-map treats it.

**Source of truth:** the registry `src/core/name_types.py`. The values are the
`PersonNameType` / `OrgNameType` Literals in `src/core/types.py` and the schema
`CHECK`s. `tests/core/test_name_types.py` holds the registry to those, to every
derived behaviour set, and to both tables below. Connecting projects read the
same registry served: `GET /api/v1/person-name-types` and
`GET /api/v1/organization-name-types` (`docs/API_PEOPLE.md`).

To add a type: the Literal and tuple, both schema `CHECK`s, a registry entry,
a row in each table here, and a minor version bump with a client regen.

## Person name types

| Type | Meaning | Use when | Not for | Example |
|---|---|---|---|---|
| `legal` | The name on government records. | A government document or an official roster gives it. | A name the person only goes by: use preferred or variant. | Patricia Ann Lee |
| `preferred` | The name the person asks to be called publicly. | The person states it, or uses it consistently in their own public material. | A short form others use that the person has not adopted: use variant. | Pat Lee, for legal Patricia Ann Lee |
| `alias` | A different name for the same person, not a form of their own name. | A pen name, a handle, an assumed name. | A short form or spelling of their own name: use variant. | Mark Twain, for Samuel Clemens |
| `former` | A name the person no longer uses, changed by marriage, divorce or choice. | Records still carry the old name. | A pre-transition name (use deadname) or a birth surname (use maiden). | Jane Smith, before becoming Jane Doe |
| `initials` | An initialism the person is known by. | The initials themselves are used as a name in public. | An abbreviated given name such as J. Lee: use variant. | JFK |
| `maiden` | The person's birth surname, before a marriage change. | Records from before the marriage carry it. | Any other earlier name: use former. | Jane Doe, née Smith |
| `religious` | A religious or monastic name. | The person took the name on entering a religious life. | A title or honorific alone: that belongs in the name's parts. | Sister Mary Joseph |
| `stage` | A performer's or artist's professional name. | The person performs or publishes art under it. | A pen name or an online handle: use alias. | Lady Gaga, for Stefani Germanotta |
| `deadname` | A pre-transition or pre-disclosure name. | Only to match records that still carry it. It lands legal_only. | A name changed for any other reason: use former. | (kept private; never shown) |
| `reading` | A phonetic reading of another name row. | A pronunciation guide (e.g. kana) for a name in another script. | A Latin-script form of the name: use romanization. | やまだ たろう, for 山田太郎 |
| `romanization` | A Latin-script rendering of another name row (pinyin, romaji). | The source name is in a non-Latin script. | A translated or adopted Western name: use alias or preferred. | Yamada Tarō, for 山田太郎 |
| `mrz` | The ICAO 9303 machine-readable-zone form of another name row. | Matching travel-document data. | Display: it is a machine form. | GARCIA<LOPEZ<<JOSE |
| `variant` | Another form of the person's own name that they legitimately use or are known by: a short form, a nickname, or a second spelling their own usage attests. | Kip for Kristopher; Becky for Rebecca; Jodi where the person's own documents use both Jody and Jodi. | A spelling only others produce, such as a transcript's guess or a typo: use misspelling. A name the person asks to be shown under: use preferred. | Kip, for Kristopher |
| `misspelling` | A spelling the person does not use, kept only so that search finds them by it. Search leads to the canonical spelling. | A transcript's guess or a common third-party typo of a name the record holds, e.g. a spelling heard in speech and never seen written. | A nickname or short form, however unusual: use variant. Never show it as an also-known-as. | Caitlin Ryan, for Kaitlyn Ryan |

### How power-map treats each type

| Type | Ladder | Auto-promote | Never canonical | Lands | Renders row | Merge-distinct | Parts | Presentable |
|---|---|---|---|---|---|---|---|---|
| `legal` | 2 | yes | no | `public` | no | no | yes | yes |
| `preferred` | 1 | yes | no | `public` | no | no | yes | yes |
| `alias` | 3 | yes | no | `public` | no | no | yes | yes |
| `former` | 7 | yes | no | `public` | no | no | yes | yes |
| `initials` | 8 | yes | no | `public` | no | no | no | yes |
| `maiden` | 6 | yes | no | `public` | no | no | yes | yes |
| `religious` | 5 | yes | no | `public` | no | no | yes | yes |
| `stage` | 4 | yes | no | `public` | no | no | yes | yes |
| `deadname` | — | no | yes | `legal_only` | no | yes | yes | no |
| `reading` | — | no | no | `public` | yes | yes | no | yes |
| `romanization` | — | no | no | `public` | yes | yes | no | yes |
| `mrz` | — | no | no | `public` | yes | yes | no | yes |
| `variant` | — | no | no | `public` | no | no | yes | yes |
| `misspelling` | — | no | yes | `public` | no | yes | no | no |

- **Ladder:** rank on the display ladder; lower wins. `—`: never picked as the
  displayed name unasked.
- **Auto-promote:** may become canonical with no hint, on insert or by
  `heal_person_canonical`.
- **Never canonical:** an `is_canonical` hint is ignored on the wire, the admin
  form refuses it, and `chk_person_canonical_name_type` refuses it in the DB.
- **Lands:** the visibility the row is stored with when sent `public`
  (`trg_deadname_visibility`).
- **Renders row:** a rendering of another name row, linked by `reading_of_id`.
- **Merge-distinct:** a merge collapses it only against the same type
  (`_NAME_IDENTITY_MATCH_SQL`), so a same-text name of another type survives.
- **Parts:** the parts normalizer suggests a decomposition (admin's Suggest). Parts
  can still be entered, or sent on the wire, for any type.
- **Presentable:** a consumer may show it as a name the person goes by.

### `variant` or `misspelling`

Both are public and searchable, never displayed unasked, and lead search to the
canonical spelling. The line between them is **attestation**:

- **`variant`:** a form of the person's own name they use or are known by — a
  short form, a nickname, a second spelling their own usage attests.
- **`misspelling`:** a spelling not attested by the person's own usage or an
  authoritative record — a transcript's guess, a common third-party typo. It is
  a search key only: **never show it as an "also known as"**, never canonical.

| Case | Type |
|---|---|
| `Kip` for Kristopher; `Becky` for Rebecca | `variant` (or `preferred` if they ask to be called it) |
| `Jody` and `Jodi`, both in the person's own documents | `variant` |
| `Jodi` only from a transcript; the record says `Jody` | `misspelling` |
| A spelling heard in a spoken self-introduction (audio cannot confirm a spelling) | `misspelling` |
| "Mark Twain" for Samuel Clemens | `alias` |

A nickname or short form is never a `misspelling`, however unusual.

`GET /people/{id}` returns misspellings in `names`, typed; read `presentable`
from the catalog before displaying any name. Search finds a misspelling and
returns the person's display name. Duplicate detection compares every
non-hidden name, so a misspelling can pair two different people; dismiss the
pair as for any other false match.

## Organization name types

| Type | Meaning | Use when | Not for | Example |
|---|---|---|---|---|
| `legal` | The organization's registered legal name. | A registry, charter or statute gives it. | A trade name: use dba. | Example Holdings, LLC |
| `dba` | A trade name the organization does business as. | It operates or registers under a name other than its legal one. | A name it no longer uses: use former. | Example Market, for Example Holdings, LLC |
| `former` | A name the organization previously used. | It was renamed and records still carry the old name. | A trade name still in use: use dba. | Example Co., before becoming Example Holdings, LLC |

No organization type has behaviour flags: an org name's display and merge
rules are in [NAMES.md](NAMES.md) §"Auto-promote invariant".
