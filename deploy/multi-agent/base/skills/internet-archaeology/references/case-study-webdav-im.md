# Case Study: WebDAV-based Instant Messenger (2001, SourceForge)

## Query

"instant messaging system" built on WebDAV, hosted on SourceForge, started ~2001. User only had description — no project name, no author.

## Investigation Steps (chronological)

1. **SourceForge archive search** — `site:sourceforge.net "WebDAV" "instant messaging"` → no relevant results. The project isn't in the SF archive at all.

2. **SourceForge project pages** — tried common URL patterns (`/projects/webdav-im`, `/projects/webdav-messenger`, `/projects/davchat`, etc.) → all 404.

3. **Wayback Machine** — checked `web.archive.org` for all URL variants: `webdav-im.sourceforge.net`, `www.webdav-im.sourceforge.net`, `sf.net/projects/webdav-im`, `sourceforge.net/projects/webdav-im` → 0 captures for any variant.

4. **Freshmeat/Freecode** — `site:freshmeat.sourceforge.net WebDAV` → no matching project. `site:freecode.com WebDAV instant messaging` → nil.

5. **Google** — `"WebDAV" "instant messaging" SourceForge` → one hit: SourceForge's own tracker discussing using WebDAV for a different purpose (OpenOffice integration). Not the project.

6. **GitHub search** — `<project name variants>` → 0 repos. No imports, no mirrors, no academic forks.

7. **Mailing list archives** — `lists.sourceforge.net` with project name guesses → nothing.

8. **Author search** — impossible without a name.

## Why It's Lost

- Project never made it to the SourceForge archive (deleted entirely, not migrated)
- No Freshmeat/Freecode listing (too small/obscure)
- No surviving code on any hosting platform
- No blog posts, reviews, or discussions mentioning it by name
- Author name unknown — no way to trace

**Most likely explanation:** A one-person hobby project, listed on SourceForge for a few months in 2001, never gained traction, author deleted it or SourceForge purged it during one of its cleanup rounds. The total audience was probably < 50 people.
