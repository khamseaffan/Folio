# Token counts for a stopped reply

Date: 2026-11-13.

## The problem

A person can stop a reply while it is streaming. Folio then closes the connection to the model provider. The provider sends its token counts only at the end of a reply, so a stopped reply arrives with no counts.

Before this change, that call was stored as "usage missing". A missing row adds nothing to the token cap. A person could start long replies, stop each one, and spend real money that the cap never saw.

## What Folio does now

When a reply is stopped, Folio counts the tokens itself and stores the result as an estimate.

- Input tokens: Folio counts the text it sent to the model for that call.
- Output tokens: Folio counts the text that arrived before the stop.
- The row is marked `estimated`. It is not marked missing.
- The expected cost is worked out from the estimate, with the same published price as a measured row.
- The token cap and the totals in settings include estimated rows.

A call that fails because of an error is still stored as missing. Folio estimates only when the person or the system stopped the call.

## Questions and answers

**Why not use the real numbers from the provider?**
The provider does not send them. OpenAI reports usage in the last piece of a stream. When Folio closes the stream early, that last piece never arrives. We looked for a way to ask afterwards. The cancel call works only for background responses, not for a live stream, and we found no official statement of what a cut stream is billed.

**Why not keep reading the reply after the person presses stop, so the real numbers arrive?**
That would give exact counts, but the model would keep writing text nobody will read. The person pays for it in their cap, and the platform pays for it in money. It also breaks a product rule: when the person stops a reply, the provider call stops.

**Why not leave the row as missing, as before?**
Because a missing row counts as zero toward the cap. Stopped replies are not free. Leaving them at zero makes the cap easy to get around and makes the usage view lower than the bill.

**Is the estimate exact?**
No. It is close for the text, and it is low for one thing: the model's hidden reasoning tokens. Folio cannot see them in a stopped reply, so they are not counted. The estimate is a lower bound that is much nearer the truth than zero.

**Why a new column, and not reuse `usage_missing`?**
The two mean different things. Missing means "we have no number". Estimated means "we have a number, and we counted it ourselves". Keeping them apart lets the admin view show how much of a total is measured by the provider and how much is our own count. A reader of the table can always tell which rows to trust to the token.

**Does this break the rule "missing usage is stored as missing, never assumed to be zero"?**
No. That rule still holds. A stopped reply is no longer missing, because Folio now has a count for it. Nothing is assumed to be zero.

**Why not estimate a failed call too?**
When a call fails, Folio does not know how far the provider got, or whether it billed anything. A stop is different: Folio knows what it sent and what it received. A failed call stays missing.

## The record

One new column on `usage`:

| Column | Type | Meaning |
| --- | --- | --- |
| `estimated` | boolean, required, default false | True when Folio counted the tokens itself because the provider sent no counts. |

A row is never both missing and estimated.

## Approaches considered

- **A. Estimate and mark it (chosen).** Described above.
- **B. Keep reading after stop.** Exact counts. Rejected: it spends tokens on text nobody reads and breaks the stop rule.
- **C. Leave it missing.** No change. Rejected: stopped replies do not count toward the cap.

## Left to the builder

- The names of functions and fields in the code.
- When the token count itself fails, the row is stored as missing.
