#!/usr/bin/env python3
"""Render the multi-agent deploy from agents.yaml.

For each agent in agents.yaml this:
  1. builds a per-agent CURATED plugins dir (render/<name>/plugins) — only the
     custom plugins + upstream bundled plugins that agent is granted — pointed at
     by HERMES_BUNDLED_PLUGINS, so nothing else can even load;
  2. builds a per-agent CURATED skills dir (render/<name>/skills) the same way,
     pointed at by HERMES_BUNDLED_SKILLS (Hermes has no skills allowlist key, so
     the filesystem IS the allowlist);
  3. seeds instances/<name>/data/{config.yaml,SOUL.md} (the per-instance
     $HERMES_HOME) by deep-merging base/config.base.yaml with the agent's
     overrides and a strict toolset/plugin allowlist;
  4. writes instances/<name>/.env.example (never touches an existing .env);
  5. emits docker-compose.generated.yml — one service per agent, shared image,
     own data volume + Telegram bot, host networking (internet + local LAN).

Add an agent = add a block to agents.yaml, run this, then
`docker compose -f docker-compose.generated.yml up -d`.

Only dependency: PyYAML.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent                 # .../hermes-agent
CUSTOM_PLUGINS_DIR = HERE / "base" / "plugins"
CUSTOM_SKILLS_DIR = HERE / "base" / "skills"
UPSTREAM_PLUGINS_DIR = REPO_ROOT / "plugins"
UPSTREAM_SKILLS_DIR = REPO_ROOT / "skills"
OPTIONAL_SKILLS_DIR = REPO_ROOT / "optional-skills"
BASE_CONFIG = HERE / "base" / "config.base.yaml"
BASE_PERSONA = HERE / "base" / "persona.base.md"   # shared behaviour prepended to every SOUL
MANIFEST = HERE / "agents.yaml"
RENDER_DIR = HERE / "render"
INSTANCES_DIR = HERE / "instances"
COMPOSE_OUT = HERE / "docker-compose.generated.yml"

# Plugin kinds that auto-load (bypass plugins.enabled); we do NOT list them in
# plugins.enabled, but they still need to be present in the curated dir.
_AUTOLOAD_KINDS = {"backend", "platform", "model-provider", "exclusive"}


def die(msg: str) -> None:
    print(f"render: ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def deep_merge(base: Any, over: Any) -> Any:
    """Recursively merge dicts; non-dicts (incl. lists) are replaced by `over`."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base.get(k), v) if k in base else v
        return out
    return over


def _memory_is_external(cfg: dict) -> bool:
    """True when memory runs in its OWN container (hindsight local_external)."""
    return cfg.get("memory", {}).get("provider") == "hindsight"


def load_yaml(path: Path) -> dict:
    if not path.exists():
        die(f"missing {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def copy_tree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def read_manifest_name_kind(plugin_dir: Path) -> tuple[str, str]:
    y = plugin_dir / "plugin.yaml"
    if not y.exists():
        y = plugin_dir / "plugin.yml"
    data = yaml.safe_load(y.read_text(encoding="utf-8")) if y.exists() else {}
    data = data or {}
    return str(data.get("name", plugin_dir.name)), str(data.get("kind", "standalone"))


def build_curated_plugins(agent: dict, out_dir: Path) -> List[str]:
    """Copy the agent's custom + bundled plugins into out_dir.

    Returns the list of names to put in plugins.enabled (auto-load kinds excluded).
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    enabled: List[str] = []

    for name in agent.get("plugins", []) or []:          # custom plugins (base/plugins)
        src = CUSTOM_PLUGINS_DIR / name
        if not src.is_dir():
            die(f"agent '{agent['name']}': custom plugin '{name}' not found in base/plugins/")
        copy_tree(src, out_dir / name)
        pname, kind = read_manifest_name_kind(src)
        if kind not in _AUTOLOAD_KINDS:
            enabled.append(pname)

    for rel in agent.get("bundled_plugins", []) or []:   # upstream bundled (repo plugins/)
        src = UPSTREAM_PLUGINS_DIR / rel
        if not src.is_dir():
            die(f"agent '{agent['name']}': bundled plugin '{rel}' not found in {UPSTREAM_PLUGINS_DIR}")
        copy_tree(src, out_dir / rel)
        pname, kind = read_manifest_name_kind(src)
        # memory / context_engine / model-provider categories are activated via
        # their own config keys (memory.provider, context.engine, model.provider),
        # NOT via plugins.enabled — never list them there.
        category = rel.split("/", 1)[0]
        if category in {"memory", "context_engine", "model-providers"}:
            continue
        # For a category path (cat/name) the loader key is the path; enable by
        # both the path key and the manifest name to be safe.
        if kind not in _AUTOLOAD_KINDS:
            enabled.append(rel if "/" in rel else pname)
    return enabled


def seed_skills(agent: dict, out_dir: Path) -> None:
    """Seed kit skills into the WRITABLE $HERMES_HOME/skills.

    The agent may edit skills (via skill_manage) and add new ones, so this is a
    copy-IF-ABSENT seed: a skill already present is left untouched to preserve
    agent edits/additions across re-renders. To force-refresh a kit skill,
    delete it under instances/<name>/data/skills/ and re-run. Skill dirs are
    flattened to their bare name (category path dropped) for local discovery.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _seed(src: Path, bare: str) -> None:
        dst = out_dir / bare
        if dst.exists():
            return
        copy_tree(src, dst)

    for rel in agent.get("skills", []) or []:            # bundled or optional-skills
        src = UPSTREAM_SKILLS_DIR / rel
        if not src.is_dir():
            src = OPTIONAL_SKILLS_DIR / rel              # fall back to optional-skills/
        if not src.is_dir():
            die(f"agent '{agent['name']}': skill '{rel}' not found in "
                f"{UPSTREAM_SKILLS_DIR} or {OPTIONAL_SKILLS_DIR}")
        _seed(src, Path(rel).name)
    for rel in agent.get("custom_skills", []) or []:     # our skills (base/skills)
        src = CUSTOM_SKILLS_DIR / rel
        if not src.is_dir():
            die(f"agent '{agent['name']}': custom skill '{rel}' not found in base/skills/")
        _seed(src, Path(rel).name)


def seed_instance(agent: dict, base_cfg: dict, enabled_plugins: List[str]) -> Path:
    name = agent["name"]
    inst = INSTANCES_DIR / name
    data = inst / "data"
    data.mkdir(parents=True, exist_ok=True)
    (inst / "secrets").mkdir(parents=True, exist_ok=True)

    # config = base <- agent.config overrides <- computed allowlist fields
    cfg = deep_merge(base_cfg, agent.get("config", {}) or {})
    toolsets = list(agent.get("toolsets", []) or [])
    # MCP is blocked by default; an agent opts in with `mcp: true`. When blocked
    # we append the `no_mcp` sentinel so no MCP server attaches to the platform.
    if not agent.get("mcp") and "no_mcp" not in toolsets:
        toolsets.append("no_mcp")
    cfg.setdefault("platform_toolsets", {})["telegram"] = toolsets
    cfg.setdefault("plugins", {})["enabled"] = enabled_plugins
    if "model" in agent:
        cfg["model"] = deep_merge(cfg.get("model", {}), agent["model"])

    # config.yaml lives OUTSIDE the writable data volume and is mounted read-only
    # into the container — the agent cannot rewrite it.
    (inst / "config.yaml").write_text(
        "# GENERATED by render.py from agents.yaml + base/config.base.yaml — edit those, not this.\n"
        + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # persona: SOUL.md = per-agent identity + shared base behaviour
    soul_rel = agent.get("soul")
    if soul_rel:
        soul_src = HERE / soul_rel
        if not soul_src.exists():
            die(f"agent '{name}': soul file '{soul_rel}' not found")
        identity = soul_src.read_text(encoding="utf-8").strip()
    else:
        identity = f"# Личность\n\nТы — {name}."
    parts = [identity]
    if BASE_PERSONA.exists():
        parts.append(BASE_PERSONA.read_text(encoding="utf-8").strip())
    (data / "SOUL.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    # per-agent custom skills seeded into $HERMES_HOME/skills (external_dirs)
    for rel in agent.get("instance_skills", []) or []:
        src = HERE / rel
        if src.is_dir():
            copy_tree(src, data / "skills" / Path(rel).name)

    # .env template (never overwrite a real .env)
    env_example = inst / ".env.example"
    lines = [
        "# Secrets for this agent — copy to .env and fill. Injected as container env.",
        "TELEGRAM_BOT_TOKEN=",
        "TELEGRAM_ALLOWED_USERS=",
        "OPENAI_API_KEY=            # gpt-5.4-mini (chat+vision) + gpt-image-2 (image gen)",
    ]
    plugset = set(agent.get("plugins", []) or [])
    if "github" in plugset:
        lines += ["GITHUB_TOKEN=              # repo scope (read+write: issues/PR create/merge)", "GITHUB_ALLOWED_REPOS="]
    if "youtrack" in plugset:
        lines += ["YOUTRACK_URL=", "YOUTRACK_TOKEN=            # permanent token; needs WRITE scope (yt_create_issue / yt_add_comment)"]
    if "jenkins" in plugset:
        lines += [
            "JENKINS_URL=                # e.g. https://jenkins.wirenboard.com",
            "JENKINS_USER=               # user email -> Pattern A (matrix-auth/WB); leave empty for token-as-user",
            "JENKINS_TOKEN=              # API token (parent-side; scrubbed from the sandbox)",
        ]
    if plugset & {"google-docs", "google-sheets"}:
        lines += ["GOOGLE_OAUTH_TOKEN=/opt/data/secrets/google-token.json"]
    if "discourse" in plugset:
        lines += [
            "DISCOURSE_URL=              # default: https://support.wirenboard.com if unset",
            "DISCOURSE_API_KEY=          # OPTIONAL. Read Only scope, single-user (see plugins/discourse/README.md) — no write tool exists in the plugin regardless",
            "DISCOURSE_API_USERNAME=     # forum username the key above is scoped to",
        ]
    if "ssh" in plugset:
        lines += [
            "SSH_ALLOWED_HOSTS=          # EMPTY/unset = ANY host (default). Set a comma-list (host/user@host) or '*' to RESTRICT.",
            "SSH_KEY=                    # leave EMPTY to use/generate ~/.ssh/id_ed25519 (ssh_keygen); or set a mounted key path",
            "SSH_USER=                   # optional default user",
            "SSH_PASSWORD=               # optional login password (else per-call `password` arg / key auth)",
        ]
    if "telegram-context" in plugset:
        lines += [
            "# telegram-context gates chats/DMs (leave TELEGRAM_ALLOWED_USERS empty — this is the gate):",
            "TELEGRAM_ADMIN_USERS=       # bot operators (user ids) allowed to enrol chats via /hermes_here etc.",
            "TELEGRAM_WORK_CHATS=        # OPTIONAL seed: chat ids where the bot RESPONDS (also add live via /hermes_here)",
            "TELEGRAM_READONLY_CHATS=    # OPTIONAL seed: read-only chat ids (also add live via /hermes_readonly)",
            "TELEGRAM_DM_EXTRA_USERS=    # extra user ids always allowed to DM (beyond auto-collected)",
        ]
    if "bitwarden" in plugset:
        lines += [
            "BW_CLIENTID=                # Bitwarden API key (Account Settings → Security → Keys)",
            "BW_CLIENTSECRET=",
            "BW_PASSWORD=                # master password (for unlock)",
            "BW_SERVER=                  # optional self-hosted / Vaultwarden URL",
        ]
    env_example.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Separate memory container gets its own .env (LLM key for extraction lives
    # HERE, not in the agent's env — the agent never sees it).
    if _memory_is_external(cfg):
        (inst / "memory.env.example").write_text(
            "# Secrets for this agent's Hindsight memory server. Copy to memory.env.\n"
            "HINDSIGHT_API_LLM_API_KEY=   # LLM key for memory extraction/synthesis\n",
            encoding="utf-8",
        )

    # arbitrary per-agent data files seeded into $HERMES_HOME (e.g. hindsight/config.json)
    for rel, content in (agent.get("data_files") or {}).items():
        dst = data / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            dst.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            dst.write_text(str(content), encoding="utf-8")
    return inst, cfg


def compose_service(agent: dict) -> dict:
    name = agent["name"]
    rel = lambda p: "./" + str(p.relative_to(HERE))
    svc: Dict[str, Any] = {
        "image": agent.get("image", "hermes-multiagent:latest"),
        "container_name": f"hermes-{name}",
        "restart": "unless-stopped",
        # Host networking: the agent needs the internet (OpenAI / GitHub / web) AND
        # the local LAN (SSH to WB controllers, mDNS discovery). No memory sidecar
        # to reach over an internal network anymore, so host mode is simplest.
        # TODO (do properly later): tighten to macvlan / explicit LAN routes instead
        # of full host networking.
        "network_mode": "host",
        "env_file": [rel(INSTANCES_DIR / name / ".env")],
        "environment": [
            "HERMES_UID=${HERMES_UID:-10001}",
            "HERMES_GID=${HERMES_GID:-10001}",
            "HERMES_GATEWAY_BOOTSTRAP_STATE=running",
            "TERMINAL_HOME_MODE=profile",
            "HERMES_BUNDLED_PLUGINS=/opt/allowed/plugins",
            "HERMES_BUNDLED_SKILLS=/opt/allowed/skills",
            # Outbound HTTP(S) proxy passthrough. The proxy URL comes from the
            # HOST env at `docker compose up` time (empty on hosts without a
            # proxy → direct). Telegram (TELEGRAM_PROXY→HTTPS_PROXY fallback),
            # OpenAI (httpx trust_env) and web tools all honour these.
            "HTTP_PROXY=${HTTP_PROXY:-}",
            "HTTPS_PROXY=${HTTPS_PROXY:-}",
            "http_proxy=${http_proxy:-}",
            "https_proxy=${https_proxy:-}",
            "ALL_PROXY=${ALL_PROXY:-}",
            # Keep loopback, the LAN (WB controllers) and mDNS .local names
            # DIRECT — never through the proxy (SSH ignores it anyway, but
            # http_fetch / .local resolution must not be proxied).
            "NO_PROXY=localhost,127.0.0.1,::1,.local,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
            "no_proxy=localhost,127.0.0.1,::1,.local,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        ]
        + [f"{k}={v}" for k, v in (agent.get("env") or {}).items()],
        "volumes": [
            f"{rel(INSTANCES_DIR / name / 'data')}:/opt/data",
            # config.yaml is WRITABLE (rw): runtime commands like /verbose persist
            # their display toggles here. No secrets live in config.yaml (those are
            # in .env). Re-render regenerates it, so agent edits reset on next deploy.
            # NOTE: deploy must chown instances/<name>/config.yaml to HERMES_UID so
            # the agent (hermes) can write it under rootless (see SERVER-DEPLOY.md).
            f"{rel(INSTANCES_DIR / name / 'config.yaml')}:/opt/data/config.yaml",
            # plugins/skills stay READ-ONLY — the agent cannot grant itself more.
            f"{rel(RENDER_DIR / name / 'plugins')}:/opt/allowed/plugins:ro",
            # bundled-skills is an EMPTY curated dir (blocks upstream image skills);
            # the agent's actual, WRITABLE skills live in /opt/data/skills (skill_manage).
            f"{rel(RENDER_DIR / name / 'bundled-skills')}:/opt/allowed/skills:ro",
            f"{rel(INSTANCES_DIR / name / 'secrets')}:/opt/data/secrets:ro",
        ],
        "command": ["gateway", "run"],
        "logging": {"driver": "json-file", "options": {"max-size": "5m", "max-file": "3"}},
    }
    dash = agent.get("dashboard")
    if dash and dash.get("port"):
        # Under host networking the dashboard binds the host port directly
        # (compose `ports:` mapping is ignored in host mode). Give each agent a
        # distinct dashboard.port so they don't collide on the host.
        svc["environment"] += [
            "HERMES_DASHBOARD=1",
            "HERMES_DASHBOARD_HOST=127.0.0.1",
            f"HERMES_DASHBOARD_PORT={int(dash['port'])}",
            "HERMES_DASHBOARD_INSECURE=1",
        ]
    return svc


def compose_memory_service(agent: dict) -> dict:
    """Hindsight memory in its OWN container (isolation): the agent talks to it
    over HTTP (local_external), so it cannot reach or corrupt the memory store's
    files/process. The extraction LLM key lives here, not in the agent's env.
    """
    name = agent["name"]
    rel = lambda p: "./" + str(p.relative_to(HERE))
    ms = agent.get("memory_server") or {}
    env = [
        f"HINDSIGHT_API_LLM_PROVIDER={ms.get('llm_provider', 'openai')}",
        f"HINDSIGHT_API_LLM_MODEL={ms.get('llm_model', 'gpt-4o-mini')}",
    ]
    if ms.get("llm_base_url"):
        env.append(f"HINDSIGHT_API_LLM_BASE_URL={ms['llm_base_url']}")
    svc: Dict[str, Any] = {
        "image": agent.get("image", "hermes-multiagent:latest"),
        "container_name": f"hermes-{name}-memory",
        "restart": "unless-stopped",
        "env_file": [rel(INSTANCES_DIR / name / "memory.env")],
        "environment": env,
        "volumes": [f"{rel(INSTANCES_DIR / name / 'memory')}:/opt/data/.hindsight"],
        # NOTE: confirm the standalone daemon command against the built image
        # (`docker run --rm hermes-multiagent:latest hindsight-embed --help`).
        # It must serve the Hindsight API on 0.0.0.0:8888. Override in agents.yaml
        # via `memory_server.command` if this default is wrong.
        "command": ms.get("command", ["hindsight-embed", "serve", "--host", "0.0.0.0", "--port", "8888"]),
        "expose": ["8888"],
        "logging": {"driver": "json-file", "options": {"max-size": "5m", "max-file": "3"}},
    }
    return svc


def main() -> int:
    manifest = load_yaml(MANIFEST)
    base_cfg = load_yaml(BASE_CONFIG)
    defaults = manifest.get("defaults", {}) or {}
    agents = manifest.get("agents", []) or []
    if not agents:
        die("no agents in agents.yaml")

    services: Dict[str, dict] = {}
    for raw in agents:
        agent = deep_merge(defaults, raw)
        if not agent.get("name"):
            die("an agent has no 'name'")
        name = agent["name"]
        print(f"render: agent '{name}'")
        enabled = build_curated_plugins(agent, RENDER_DIR / name / "plugins")
        # empty curated bundled-skills dir → blocks upstream image skills (allowlist)
        empty_skills = RENDER_DIR / name / "bundled-skills"
        if empty_skills.exists():
            shutil.rmtree(empty_skills)
        empty_skills.mkdir(parents=True, exist_ok=True)
        # kit skills go into the WRITABLE $HERMES_HOME/skills (agent may edit/add)
        seed_skills(agent, INSTANCES_DIR / name / "data" / "skills")
        cfg = seed_instance(agent, base_cfg, enabled)[1]
        services[f"hermes-{name}"] = compose_service(agent)
        if _memory_is_external(cfg):
            services[f"hermes-{name}-memory"] = compose_memory_service(agent)
            print("  memory          = external container hermes-%s-memory" % name)
        print(f"  plugins.enabled = {enabled}")
        _ts = list(agent.get("toolsets") or [])
        if not agent.get("mcp"):
            _ts.append("no_mcp")
        print(f"  toolsets        = {_ts}")

    COMPOSE_OUT.write_text(
        "# GENERATED by render.py — do not edit. Edit agents.yaml and re-run.\n"
        + yaml.safe_dump({"services": services}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"render: wrote {COMPOSE_OUT.name} with {len(services)} service(s)")
    print("next: fill instances/<name>/.env, then "
          "`docker compose -f docker-compose.generated.yml up -d`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
