# X relationship metadata and feed grouping

## Contract

Full collected body remains in Content.body_html/body_text/excerpt. Scweet's normalized
record continues to supply that body unchanged. SourceEntry.raw_metadata.x adds a
versioned, allowlisted relationship projection; upstream raw cookies/envelopes are not
persisted. It includes author ID/handle, conversation ID, explicit reply target and its
author ID, one quoted/reposted target (including collected target text), and evidence
completeness. Missing quoted objects are `unavailable`, not asserted deleted/private.

GET /v1/feed and /v1/feed/page add nullable `x_preview` (non-X entries remain null):

- tweet_id, author {id, handle, name, avatar_url}, external_url
- text: at most 180 grapheme clusters plus ellipsis; no full-text translation claim
- completeness: parsed (raw legacy inspected), partial (fallback fields), legacy (old row)
- reply_to: optional {tweet_id, author_id, handle}
- quote/repost: optional {tweet_id, author, text, external_url, availability}; text
  at most 80 grapheme clusters plus ellipsis, one level only
- is_repost: when true outer author is the reposting actor; nested repost author/text
  belong to the original author. Do not display repost text as the actor's own opinion.
- thread: optional {loaded_count, root_tweet_id, root_url}. Count refers only to collected
  posts in this source. It does not claim total thread size or completeness.

Original FeedItem fields remain compatible. UI must enforce its own four-line/two-line
limits and use actual external_url/root_url to open X. Old records get short previews,
no invented relationship. Saved content remains an actual source-owned Content; saving
the root does not save every thread member. Previously saved children remain in Saved.

## Grouping and pagination

Authorization, enabled subscriptions, source and folder scope, and Content de-duplication
are applied before grouping and the keyset page boundary. An edge requires an explicit
reply ID, actual parent in the same source, matching nonempty author IDs and no repost
at either end. If upstream reply-author ID exists, it must also match the parent.
Conversation ID alone and handles/time similarity are never evidence. Quoting another
post is independent of the reply edge and never merges the quoted target into a thread.

Recursive SQL propagates collected root IDs down edges; it does not form a quadratic
ancestor closure. Each valid post produces at most one mapping. Cycles and their attached
children have no reachable root and remain standalone. Missing parents are collected
roots; late-arriving parents automatically regroup on the next query without data repair.

A root's existing feed_sort_at/id determines position and cursor; new continuations do
not bump it. This deliberately favors stable scrolling over resurfacing old threads.
Keyset pagination is not a snapshot: a late parent can change the group representative
while a client is already paging. Refresh from the first page after ingestion to see the
new grouping; do not claim snapshot consistency across concurrent data changes.

The query scans the authorized scope, even for a small page. The integration test covers
300 members in one chain; this is not a production-scale SLA. Very large histories should
be measured before rollout; durable root indexing is a possible future optimization.

## Deploy and repair

No DDL migration or schema revision is needed. Install the updated Python project/lock
(regex is now explicit, previously transitive); deploy the changed API, normalization,
projection/grouping and worker metadata-preservation files together. Restart API and
worker using the existing service process, preserving other remote uncommitted code.
Updating the collector service or obtaining more tweet body text is unnecessary.

Normal incremental scans reprocess changed candidate hashes, allowing newly encountered
records to acquire metadata. A later partial provider fallback cannot erase parsed
relationships. Old rows cannot recover discarded raw fields from title/text alone.

Existing offline Scweet TweetRecord JSON can repair matched existing rows:

```sh
cd backend
.venv/bin/python -m app.ingestion.x_relationship_backfill \
  --source-id SOURCE_UUID --input /private/path/scweet-output.json
# Inspect count-only dry run. Add --apply to write the same batch.
```

Accepts an array or an object with items/tweets array, at most 20 MiB per batch. The
explicit source must be X. Matches by (source_id,native_id), skips already-parsed rows,
never inserts content or modifies stored body, and performs one transaction. It does
not call Scweet/X. Run under the existing database environment without echoing secrets.
If files do not match existing native IDs the updated count is zero: this is not a
successful full backfill. Keep the private source JSON out of version control.

Historical real samples prove quoted/repost objects and reply IDs exist, but a real
same-author continuation sample remains unverified while Scweet is rate-limited. Do not
retry collection just to satisfy a visual demo. Test fixtures validate grouping rules;
label fixture-backed design previews separately from real user data.
