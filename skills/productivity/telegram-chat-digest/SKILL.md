---
name: telegram-chat-digest
description: "Build a topic report from Telegram chat history over time."
version: 1.0.0
author: Alexander Degtyarev + Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [telegram, digest, report, sessions, delegate_task, group-chat]
    category: productivity
    related_skills: [hermes-agent]
    requires_toolsets: [terminal, delegation]
---

# Telegram Chat Digest

Builds a report/digest of a Telegram chat's history over a time window
(a week, a month, half a year), optionally narrowed to a topic. Uses
`hermes sessions digest` to page through raw messages — including
"observed" group chatter the bot never directly replied to — and does the
actual summarizing as a normal conversation turn or, for large windows,
inside an isolated `delegate_task` subagent so the raw history never
bloats the main session's cached context.

This skill is **not** for real-time chat monitoring or for a single
recent-messages lookup — `session_search` already covers ad-hoc keyword
search within the live conversation window.

## When to Use

The user asks for a report, summary, recap, or digest of what happened
in a Telegram chat/group over some period — generally, or about a
specific topic. Examples: "what did people say about the deploy this
week", "summarize the last month of the project chat", "собери отчёт по
теме X за полгода".

## Prerequisites

- The chat's group chatter is only recorded if
  `telegram.observe_unmentioned_group_messages` (alias
  `ingest_unmentioned_group_messages`) is enabled and the chat is listed
  in `telegram.group_allowed_chats` in `config.yaml`. If it isn't, the
  digest will only contain turns that directly triggered the bot — say so
  explicitly rather than presenting a partial history as complete.
- For a chat that exists purely to be a reporting source (the bot should
  never post there, period), recommend `telegram.read_only_chats` instead:
  it's self-sufficient (no separate `group_allowed_chats`/
  `observe_unmentioned_group_messages` needed) and, unlike the setting
  above, gives a hard code-level guarantee that even a direct @mention or
  reply to the bot in that chat never produces a reply — while still
  capturing everything for the digest.
- The target `chat_id`. If the user doesn't know it, find it with
  `hermes sessions list --source telegram` (via `terminal`) and match on
  the chat's title/preview, or ask the user.

## How to Run

1. Learn the size of the window before pulling any content:
   ```bash
   hermes sessions digest --chat-id <id> --source telegram \
     --since 7d --count-only
   ```
   Returns `{"matched_sessions", "total_count", "oldest_ts", "newest_ts",
   "approx_chars"}` — no message content, so it's cheap even for a
   half-year window.
2. Decide small vs. large (see Quick Reference) based on `total_count`.
3. Page through with `--cursor`/`--limit`, reading the `=== META {...}
   ===` footer line (text format) or the final `{"_meta": {...}}` line
   (jsonl format) after each call for `has_more`/`next_cursor`.
4. Summarize — inline for a small result, via `delegate_task` for a large
   one (see Procedure) — and present the report to the user.

## Quick Reference

| Situation | Approach |
|---|---|
| `total_count` roughly under ~1500 messages / a few hundred KB (`approx_chars`) | Call `hermes sessions digest` directly via `terminal`, page inline, summarize in the current turn. |
| Weeks/months, thousands of messages (e.g. a half-year window) | Use `delegate_task` — see Procedure step 3. Never pull the full raw history into the main conversation; that permanently bloats its cached prefix. |
| Bot's own replies matter too | Add `--roles user,assistant` (default is `user` only). |
| Narrowing by topic | Add `--query "<term>"` — a plain case-insensitive substring match, not full search syntax. Keep it identical between the `--count-only` call and every paging call. |

## Procedure

1. `terminal`: `hermes sessions digest --chat-id <id> --source telegram
   --since <window> [--query "<topic>"] --count-only` — read `total_count`.
2. **Small result**: `terminal` calls to `hermes sessions digest ...
   --cursor <n> --limit 300`, looping while `has_more=true` (start
   `--cursor 0`), then summarize the topic yourself from the accumulated
   text in this turn.
3. **Large result**: call `delegate_task` instead of paging in the main
   turn:
   - `goal`: state the exact `chat_id`, `source`, window, and topic; tell
     the subagent to run `hermes sessions digest` in a loop via
     `terminal`, starting `--cursor 0`, incrementing to each batch's
     `next_cursor` while `has_more=true`, summarizing each batch against
     the requested topic, folding it into one running summary, and
     discarding the raw batch text as it goes. It must return **only**
     the final structured report — not raw messages, not per-batch notes.
   - `toolsets: ["terminal"]`
   - `role: "leaf"` (default — no nested delegation needed)
   - `background: true` is a reasonable option when the user doesn't need
     the report synchronously (a half-year digest can take a while).
4. Present the returned report to the user. Do not paste raw per-batch
   dumps into the main conversation at any point.

## Pitfalls

- **chat_id vs. display name.** `--chat-id` is the numeric Telegram chat
  ID, not the chat's display name — resolve it via `hermes sessions list`
  first if the user only gave a name.
- **No observed history configured.** If
  `observe_unmentioned_group_messages`/`group_allowed_chats` isn't set for
  that chat, `total_count` will only reflect triggered turns. Tell the
  user the digest is partial rather than silently reporting on less data
  than they expect.
- **Roles default excludes the bot.** `--roles` defaults to `user` only —
  add `assistant` if the report should also reflect the bot's own
  answers.
- **Keep filters consistent across calls.** `--query`/`--since`/`--until`
  must be identical between the `--count-only` call and every paging call,
  or the running total won't match what you actually collected.
- **Don't inline a large digest.** The whole point of the `delegate_task`
  path is keeping raw chat logs out of the main session's cached prefix —
  skipping it for a "quick check" on a big window defeats the purpose and
  makes every subsequent turn in that conversation more expensive.

## Verification

Sum of `returned` across all pages should equal the `total_count` from
the `--count-only` call for the same filters, and `has_more` should end
`false` on the last page. If a `delegate_task` subagent was used, confirm
its final message is the report itself, not a page of raw messages or a
partial summary that stopped before `has_more` went `false`.
