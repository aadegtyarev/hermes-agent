---
name: internet-archaeology
description: Systematic investigation of abandoned, lost, or historically significant open-source projects from the 2000s era — finding surviving traces when primary sources are gone.
triggers:
  - user asks about an old open-source project that seems to have vanished
  - user wants to recover history of a 1990s-2000s era project
  - user provides a project name and description but can't find any current trace
  - user asks "what happened to project X" and it predates GitHub migration
---

# Internet Archaeology — Investigating Lost Open-Source Projects

Systematic methodology for recovering traces of abandoned open-source projects from the 1990s-2000s era, when SourceForge was the primary host, personal project sites were common, and many projects left no surviving code.

## Methodology (ordered by likelihood of finding something)

### 1. SourceForge Archive

SourceForge migrated old inactive projects to an archive. Many still have read-only pages.

```
https://sourceforge.net/projects/<project-name>/
```

Look for:
- Project page itself (may redirect to archive)
- File releases (download listings)
- Mailing list archives (lists.sourceforge.net)
- Tracker (bugs, feature requests)
- SVN/CVS read-only access if preserved

If the project name is unknown — search SourceForge directly:
```
site:sourceforge.net "project description phrase" "instant messaging"
```

### 2. Wayback Machine (archive.org)

Check multiple URL variants for the project:

```
https://web.archive.org/web/*/<project-name>.sourceforge.net
https://web.archive.org/web/*/<project-name>.sf.net
https://web.archive.org/web/*/www.<project-name>.sourceforge.net
https://web.archive.org/web/*/<project-name>.berlios.de
https://web.archive.org/web/*/www.<project-name>.org
```

Try the project name as both subdomain and path: `sourceforge.net/projects/<name>` and `<name>.sourceforge.net`.

### 3. Freshmeat / Freecode Archive

Freshmeat (later Freecode) was the primary directory of open-source software in the 2000s. Archives exist:

```
https://freshmeat.sourceforge.net/projects/<project-name>
```

Search: `site:freecode.com "<project-name>"` or `site:freshmeat.sourceforge.net "<project-name>"`

### 4. Mailing List Archives

If the project had public mailing lists:

- **SourceForge lists:** `https://sourceforge.net/p/<project-name>/mailman/`
- **Gmane (archived):** `https://gmane.io/` — check for project lists
- **Google Groups:** `site:groups.google.com "<project-name>" "mailing list"`
- **Marc.info:** `site:marc.info "<project-name>"`

### 5. GitHub / GitLab Search

The project may have been imported post-factum:

```
https://github.com/search?q=<project-name>&type=repositories
https://gitlab.com/search?search=<project-name>
```

Check for:
- Direct imports of the original code
- Forks or mirrors with different names
- "import from sourceforge" commits
- Author's personal namespace imports

### 6. Author / Maintainer Search

Search the maintainer's name/email/handle:

- GitHub profile for imported repos
- LinkedIn for mentions of the project
- Hacker News / Lobsters discussions
- Stack Exchange profiles
- Personal blogs (Wayback Machine)
- `site:news.ycombinator.com "<project-name>"`

### 7. Contemporary References

The project likely appeared in:

- **Slashdot** — `site:slashdot.org "<project-name>"`
- **Linux.com / Linux Today** — now archived, search via Wayback
- **LWN.net** — `site:lwn.net "<project-name>"`
- **Freshmeat announcements** — `site:freshmeat.sourceforge.net "<project-name>"`
- **Personal blog posts** from the era, via Wayback + Google

### 8. Downloads / Binaries

Even if source is lost, binaries may survive:

- SourceForge file releases
- `archive.org/details/<project-name>` (full ISO/disc images)
- Third-party package repos (Debian snapshot, FreeBSD ports)
- Tucows / Download.com archives via Wayback

## When to Conclude "Lost"

After exhausting the above checks, the project is likely lost if:

1. SourceForge page returns 404 (not even archive redirect)
2. Wayback Machine has 0 captures for any URL variant
3. No Freshmeat/Freecode record exists
4. GitHub/GitLab have 0 repos matching the name or description
5. Author is untraceable
6. No blog posts, forum mentions, or mailing list archives survive

**Cause analysis:** Loss typically happens when:
- Project was hosted on a personal domain that expired (not SourceForge)
- SourceForge project was deleted entirely (not just archived)
- Project predated widespread code hosting and the author's hard drive died
- Author used a throwaway email/handle and vanished
- Project was very small (single developer, < 100 downloads) — no one cared to preserve it

## Output Format

Report findings as:

```
## Project: <name>

**Status:** Lost / Traces found / Partial recovery

**Evidence checked:**
- SourceForge: [found 404 / archive page / file releases]
- Wayback Machine: [0 captures / N captures at URLs ...]
- Freshmeat: [found / not found]
- GitHub: [0 results / N repos]
- Author search: [untraceable / found at ...]
- Contemporary refs: [none / N mentions]

**Conclusion:** Brief summary of what existed, what's lost, and why.
```
