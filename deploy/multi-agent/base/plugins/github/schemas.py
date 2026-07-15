"""Tool schemas for the GitHub plugin — what the LLM sees."""

ISSUE_LIST = {
    "name": "github_issue_list",
    "description": (
        "List issues in a GitHub repository with filtering. "
        "Returns issue number, title, state, labels, assignee, and URL. "
        "Use this to survey open issues, check what needs attention, "
        "or find issues by label/assignee. "
        "For private/allowlisted repos (needs `gh` auth or GITHUB_TOKEN); "
        "if it fails and the repo is public, curl/http_fetch also works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Filter by state. Default: open",
            },
            "labels": {
                "type": "string",
                "description": "Comma-separated label names to filter by (e.g. 'bug,urgent')",
            },
            "assignee": {
                "type": "string",
                "description": "Filter by assignee username (or 'none' for unassigned, '*' for any)",
            },
            "limit": {
                "type": "integer",
                "description": "Max issues to return. Default: 20",
            },
            "search": {
                "type": "string",
                "description": "Free-text search in issue title and body",
            },
        },
        "required": ["repo"],
    },
}

ISSUE_VIEW = {
    "name": "github_issue_view",
    "description": (
        "View a single GitHub issue with its description and recent comments. "
        "Use this to read an issue in detail before responding or taking action. "
        "For private/allowlisted repos (needs `gh` auth or GITHUB_TOKEN); "
        "if it fails and the repo is public, curl/http_fetch also works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "number": {
                "type": "integer",
                "description": "Issue number",
            },
            "include_comments": {
                "type": "boolean",
                "description": "Include recent comments. Default: true",
            },
        },
        "required": ["repo", "number"],
    },
}

ISSUE_CREATE = {
    "name": "github_issue_create",
    "description": (
        "Create a new GitHub issue. "
        "Use this to file bugs, feature requests, or tasks. "
        "Needs `gh` auth or GITHUB_TOKEN (write op, no public-repo fallback)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "title": {
                "type": "string",
                "description": "Issue title",
            },
            "body": {
                "type": "string",
                "description": "Issue body (markdown supported)",
            },
            "labels": {
                "type": "string",
                "description": "Comma-separated label names to apply",
            },
            "assignee": {
                "type": "string",
                "description": "GitHub username to assign",
            },
        },
        "required": ["repo", "title"],
    },
}

PR_LIST = {
    "name": "github_pr_list",
    "description": (
        "List pull requests in a GitHub repository with filtering. "
        "Returns PR number, title, state, author, branch names, draft status, "
        "and whether CI checks passed. Use this to see what's open for review. "
        "For private/allowlisted repos (needs `gh` auth or GITHUB_TOKEN); "
        "if it fails and the repo is public, curl/http_fetch also works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "state": {
                "type": "string",
                "enum": ["open", "closed", "merged", "all"],
                "description": "Filter by state. Default: open",
            },
            "author": {
                "type": "string",
                "description": "Filter by PR author username",
            },
            "labels": {
                "type": "string",
                "description": "Comma-separated label names",
            },
            "limit": {
                "type": "integer",
                "description": "Max PRs to return. Default: 10",
            },
        },
        "required": ["repo"],
    },
}

PR_VIEW = {
    "name": "github_pr_view",
    "description": (
        "View a single pull request in detail: description, changed files summary, "
        "review status, CI check status, mergeable state, and recent review comments. "
        "Use this before deciding to merge or request changes. "
        "For private/allowlisted repos (needs `gh` auth or GITHUB_TOKEN); "
        "if it fails and the repo is public, curl/http_fetch also works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "number": {
                "type": "integer",
                "description": "PR number",
            },
        },
        "required": ["repo", "number"],
    },
}

PR_MERGE = {
    "name": "github_pr_merge",
    "description": (
        "Merge a pull request. Can use merge commit, squash, or rebase. "
        "Optionally delete the source branch after merge. "
        "IMPORTANT: always confirm with the user before merging, "
        "and check that CI passed and reviews are approved first. "
        "Needs `gh` auth or GITHUB_TOKEN (write op, no public-repo fallback)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "number": {
                "type": "integer",
                "description": "PR number to merge",
            },
            "method": {
                "type": "string",
                "enum": ["merge", "squash", "rebase"],
                "description": "Merge method. Default: merge",
            },
            "delete_branch": {
                "type": "boolean",
                "description": "Delete source branch after merge. Default: false",
            },
        },
        "required": ["repo", "number"],
    },
}

REPO_SEARCH = {
    "name": "github_repo_search",
    "description": (
        "Search across a GitHub repository: code, issues, and PRs. "
        "Returns matches with URLs. "
        "Use this to find relevant code, track down where a feature was discussed, "
        "or find issues/PRs mentioning a specific topic. "
        "For private/allowlisted repos (needs `gh` auth or GITHUB_TOKEN); "
        "if it fails and the repo is public, curl/http_fetch also works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (e.g. 'aadegtyarev/hermes-plugins')",
            },
            "query": {
                "type": "string",
                "description": "Search query (supports GitHub search syntax)",
            },
            "type": {
                "type": "string",
                "enum": ["code", "issues", "prs", "all"],
                "description": "What to search. Default: all",
            },
            "limit": {
                "type": "integer",
                "description": "Max results per type. Default: 10",
            },
        },
        "required": ["repo", "query"],
    },
}
