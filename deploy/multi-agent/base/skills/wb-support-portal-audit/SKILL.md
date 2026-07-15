---
name: wb-support-portal-audit
description: "Automated quality monitoring of tech-support responses on support.wirenboard.com (Discourse). Collect recent topics, score staff response quality against an 8-metric framework, cross-check bug topics against YouTrack, and produce a periodic digest. Self-contained: Discourse API, workflow, metrics, digest format and cron setup all live here."
trigger: "User asks to evaluate or audit tech-support quality, check who answered what, measure response times, or generate a support performance digest."
---

# wb-support-portal-audit

Periodic audit of tech-support quality on the public Discourse forum at
`support.wirenboard.com`. Measures speed, completeness, tone and resolution of
staff responses, flags bugs that never became tickets, and emits a digest.

The forum is public and read-only for this skill — assessment is from
public-facing responses only. No credentials are required.

---

## 1. Discourse API

Public JSON API, no auth. Base: `https://support.wirenboard.com`.

| Endpoint | Returns |
|----------|---------|
| `GET /latest.json` | `topic_list.topics[]` — recent topics sorted by activity |
| `GET /t/{id}.json` | Full topic: `post_stream.posts[]` with every post |
| `GET /t/{id}/posts.json` | Posts only (lighter payload) |

Useful fields:

- **Topic list** (`topic_list.topics[]`): `id`, `title`, `created_at`,
  `bumped_at` (recency filter), `last_posted_at`, `posts_count`,
  `reply_count`, `pinned_globally` (exclude stickies), `last_poster_username`.
- **Topic detail**: `id`, `title`, `created_at`, `accepted_answer.post_number`
  (set = marked solved), and `post_stream.posts[]` where each post has
  `username`, `created_at`, `cooked` (HTML body), `staff` (bool), `post_number`.

Timestamps are ISO8601 UTC (`2026-07-01T12:08:41.000Z`) — normalize to MSK (+3h)
for the digest.

**Staff identification.** Each post has `staff: true/false`. Some employees may
not carry the `staff` flag if their forum role isn't configured — keep a list of
known staff usernames per instance and cross-check against it.

Example — pull staff posts from a topic:

```python
# execute_code
import json, urllib.request
req = urllib.request.Request(
    f"https://support.wirenboard.com/t/{topic_id}.json",
    headers={"User-Agent": "wb-support-audit"},
)
data = json.load(urllib.request.urlopen(req))
for p in data["post_stream"]["posts"]:
    if p.get("staff"):
        print(f"{p['username']} @ {p['created_at']}: {p['cooked'][:200]}")
```

---

## 2. Workflow

### 2.1 Collect topics

Fetch `GET /latest.json`, then filter:

- `bumped_at` within the audit window (last 24h, or since the previous run);
- drop `pinned_globally == true` and welcome/announcement stickies.

### 2.2 Read staff responses

For each active topic fetch `GET /t/{id}.json` and:

- find the first staff reply (earliest post with `staff == true`);
- **TTR** (time to first reply) = `first_staff_post.created_at − topic.created_at`;
- collect all staff posts and their bodies for scoring.

Business-hours note: a topic opened at night/weekend and answered next business
morning has large wall-clock TTR but near-zero business-hours TTR — score by
business hours and note the boundary, don't flag it as a violation.

### 2.3 Cross-source validation

Discourse alone can't reveal **RCA gaps** (confirmed bugs with no ticket) or
whether a dismissed client was followed up. Cross-reference:

- **YouTrack** — for each topic describing a confirmed bug/regression, search for
  a matching ticket: `project: SUP created: <range>`, `project: SOFT created: <range>`.
  No ticket found → RCA gap; log it in the digest with the topic link.
- **Internal support Telegram chats** (chat ids configured per instance) — the
  notifications feed mirrors Discourse activity; the internal coordination chat
  shows escalations and planning discussion. Use these only to confirm
  follow-through and escalation behaviour, never to quote internal messages in
  the digest.

---

## 3. 8-metric audit framework

Each metric maps to a concrete data source with a PASS/WARN/FAIL boundary.

| # | Metric | How to measure | Threshold | Source |
|---|--------|----------------|-----------|--------|
| 1 | **Response SLA** | Median business-hours time from `topic.created_at` to first staff reply | ≤3 working hours; hard-fail on any topic silent >3 working days | Discourse `created_at` → first staff `created_at` |
| 2 | **Human-terminal** | % replies asking the user to run terminal commands (`ssh`, `наберите`, `выполните`, `в консоли`) instead of offering a Cloud/AnyDesk connection | <10%; ideally 0% | Text grep staff posts: positive `облак`, `AnyDesk`, `wb-cloud`; negative `ssh`, `консол`, `наберите` |
| 3 | **Client abandonment** | % topics where the last client message got no staff reply for >7 days; spot-check dismissed clients for follow-up | 0% over 7d | Discourse `last_posted_at` vs solved marker; manual spot-check on emotive threads |
| 4 | **RCA / 8D on bugs** | % bug/regression topics that produced a YT ticket (`SUP-`/`SOFT-`/`WB-`); of those, % with a root-cause comment | 100% have a ticket; >80% have RCA | Cross-reference Discourse ↔ YT; look for `причин`, `8D`, `root cause` in YT comments |
| 5 | **Department autonomy** | Escalations that skipped the level-1/2 ladder and went straight to senior management | ≤3/month; 0% topics escalated to senior management first | Telegram internal chat + Discourse staff posts |
| 6 | **"Works for me" ban** | % replies with `работает`/`нормально`/`не воспроизводится` and no preceding diagnostic request or different-config caveat | 0% — zero tolerance | Text grep; whitelist if preceded by `архив`, `лог`, `диаг-архив`, `верси`, or an explicit caveat |
| 7 | **Procedure adherence** | % topics where a documented procedure was silently violated (skipped a diagnostic step, wrong escalation, no Cloud) without raising it for a systematic fix | 0% — zero tolerance | Manual spot-check vs internal docs |
| 8 | **Wiki reference rate** | % topics with at least one staff link to `wiki.wirenboard.com` / `wb.wiki` | >40% target | Text grep staff posts for wiki URLs |

### Scoring

| Level | Condition | Action |
|-------|-----------|--------|
| 🟢 Green | all 8 within threshold | note in digest |
| 🟡 Yellow | 1–2 above threshold | flag for planning discussion |
| 🔴 Red | 3+ above threshold, or any zero-tolerance metric (2/6) breached | trigger a root-cause (8D) investigation |

### Calibrating the criteria

The framework above is the production default. When onboarding a new audit or a
different reviewer, re-derive criteria from what the people who own support
quality actually complain about — their recurring frustrations are more
diagnostic than any generic rubric:

1. Gather the reviewer's own messages about support (Telegram history / mentions).
2. Extract what they repeatedly care about — anger and sarcasm mark patterns they
   dislike; direct demands mark explicit expectations; pointed questions mark gaps.
3. Compare against the team's official support docs.
4. The **gaps** — things a reviewer is vocal about but the docs are silent on —
   are the highest-value audit criteria; they are unspoken expectations staff
   aren't trained on. Turn each into a measurable metric like those above.

Keep derived criteria abstract and measurable. Do not paste verbatim internal
messages, names, or chat ids into the skill or the digest.

---

## 4. Digest format

Mono-block, no emoji in the body except the metric traffic-lights:

```
**Audit DD.MM.YYYY**
- Active topics: N
- Replied: username (N), username (N)
- No reply over 24h: title (#id)
- Average first response time: Xh
- Problematic: title (#id) — reason

**8-metric dashboard**
1. SLA: 🟢/🟡/🔴 (details)
2. Human-terminal: 🟢 (0 violations)
3. Abandonment: 🟢
4. RCA/tickets: 🔴 (N bugs, M tickets — gap list)
5. Autonomy: 🟢
6. Works-for-me: 🟡 (#id)
7. Procedures: 🟢
8. Wiki rate: 🟡 (X/N topics)

**RCA gaps** (if any)
- title (#id) — no YT ticket, reason

**YouTrack SUP snapshot** (created last N days)
- SUP-XXX — summary
```

Weekly roll-up adds:

```
**Weekly metrics**
- Total topics: N
- Avg response time: Xh
- Closed with solution: N (X%)
- Fastest / slowest responder
- RCA gap rate: N bugs → M tickets (X%)
```

---

## 5. Cron setup

- Job name: `support-audit`
- Schedule: `0 18 * * 1-5` (weekdays 18:00 MSK)
- The cron prompt must be self-contained: fetch latest topics, score, deliver the
  digest. Attach this skill to the job.

---

## 6. Pitfalls

- **Public only.** Private staff notes aren't in the API. Score public responses.
- **Solution marker is unreliable.** Many resolved topics never get the
  accepted-answer flag. Infer from content: `спасибо, помогло`, `заработало`, `всё ок`.
- **RCA gap is the top blind spot.** A topic can be "resolved" for the client
  (workaround, coupon) while hiding an unfixed bug. Metric 4 requires actively
  cross-checking YouTrack — a `купон`/`workaround` resolution with no ticket is a
  process failure.
- **"Works for me" is easy to skim past.** "попробовал воспроизвести — всё работает"
  reads as helpful; check whether they reproduced on the exact config/hardware
  revision the user reported. If not, flag it.
- **Rate limits.** No-auth Discourse may throttle. Keep ≥1s between topic fetches
  (≥15 min between full audit passes).
- **Telegram cache lag.** The internal chat may show zero cached posts even when
  active. Prefer the notifications feed for Discourse mirroring; for the internal
  chat try `search(deep=true)` / `history(1d)` and check `fetch_status`.
