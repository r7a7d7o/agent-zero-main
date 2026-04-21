---
name: a0-create-agent
description: Create a new Agent Zero agent profile (subordinate). Covers where profiles live (user / plugin-distributed / project-scoped), the agent.yaml schema, the prompt inheritance & override model, and optional profile-specific tools and extensions. Use for any "create/add/new agent profile" request.
version: 1.0.0
tags: ["agents", "profile", "create", "new", "subordinate"]
trigger_patterns:
  - "create agent"
  - "new agent profile"
  - "add agent profile"
  - "make agent profile"
  - "agent profile template"
  - "build agent profile"
---

# Create an Agent Zero Agent Profile

> [!IMPORTANT]
> Do **not** create new profiles in `/a0/agents/` — that directory is reserved for core framework profiles (`default`, `agent0`, `developer`, `hacker`, `researcher`, `_example`). User profiles belong in `/a0/usr/agents/<profile_name>/`.

Related skills: `/a0/skills/a0-development/SKILL.md` (broader framework guide) | `/a0/skills/a0-create-plugin/SKILL.md` (bundle a profile inside a plugin).

Primary references:
- `/a0/agents/_example/` — the canonical reference profile (tool + extension + prompt overrides)
- `/a0/agents/default/` — the base profile every other profile inherits from
- `/a0/docs/agents/AGENTS.plugins.md` — plugin-distributed profiles + per-profile config

---

## Step 0: Ask First — Where should this profile live?

Before creating anything, ask the user one question:

> "Where should this profile live?
> 1. **User profile** — `/a0/usr/agents/<name>/` (survives framework updates, this is the normal choice).
> 2. **Plugin-distributed** — shipped with a plugin at `/a0/usr/plugins/<plugin>/agents/<name>/` (for reusable profiles tied to a plugin's tools).
> 3. **Project-scoped** — `project/.a0proj/agents/<name>/` (only available inside that project)."

Pick the path based on the answer. The rest of the skill uses `<PROFILE_ROOT>` for whichever was chosen.

---

## Step 1: Collect the four inputs

Gather these before writing any files:

| Input | Rule | Example |
|---|---|---|
| **name** (directory name) | lowercase letters, numbers, hyphens or underscores; must be unique across profile search paths | `data-analyst` |
| **title** | human-readable display name shown in the UI | `Data Analyst` |
| **description** | one-line specialization summary | `Agent specialized in data analysis, visualization, and statistical modeling.` |
| **context** | instructions telling the *superior* agent when to delegate to this profile | `Use this agent for data analysis tasks, creating visualizations, statistical analysis, and working with datasets in Python.` |

> [!NOTE]
> `agent.yaml` has **only** these three content fields (`title`, `description`, `context`). There is no per-profile model, temperature, or `allowed_tools` setting — model config is handled by the `_model_config` plugin, and tool availability is controlled by plugin activation. Do not invent extra fields.

---

## Step 2: Create the directory and `agent.yaml`

```
<PROFILE_ROOT>/<name>/
├── agent.yaml                # Required
├── prompts/                  # Optional — prompt overrides
├── tools/                    # Optional — profile-specific tools
└── extensions/               # Optional — profile-specific extensions
```

`agent.yaml`:

```yaml
title: Data Analyst
description: Agent specialized in data analysis, visualization, and statistical modeling.
context: Use this agent for data analysis tasks, creating visualizations, statistical
  analysis, and working with datasets in Python.
```

A profile with only `agent.yaml` is valid — it inherits everything from `default/`. Add the sections below only when you need to change something.

---

## Step 3: Override prompts (the most common customization)

Profiles inherit all prompts from `/a0/prompts/` and from `/a0/agents/default/`. To change behavior, drop a file with the **same filename** into `<PROFILE_ROOT>/<name>/prompts/`. The loader searches profile-specific prompts first and falls back to the defaults.

### The canonical override: `agent.system.main.specifics.md`

This is the designated extension slot for profile-specific role, identity, and behavior instructions. The file ships **empty** in both `/a0/prompts/agent.system.main.specifics.md` and `/a0/agents/default/agent.system.main.specifics.md` precisely so profiles can fill it in without fighting the base prompt. It is included from `agent.system.main.md` right after `agent.system.main.role.md`, so whatever you put here layers on top of the inherited role.

**Every shipped profile in `/a0/agents/` overrides this file** — a good sanity check that this is the right place for your specialization. Look at the existing profiles for concrete shape:

| Profile | What its `agent.system.main.specifics.md` does |
|---|---|
| `/a0/agents/agent0/prompts/agent.system.main.specifics.md` | Establishes the top-level user-facing agent's behavior |
| `/a0/agents/developer/prompts/agent.system.main.specifics.md` | Full "Master Developer" role + process spec (most elaborate example) |
| `/a0/agents/hacker/prompts/agent.system.main.specifics.md` | Concise red/blue team pentester identity |
| `/a0/agents/researcher/prompts/agent.system.main.specifics.md` | Research methodology and deliverable expectations |
| `/a0/agents/_example/prompts/agent.system.main.specifics.md` | Minimal demo override (fictional "Agent Zero" persona) |

Start by copying whichever existing profile's `specifics.md` is closest to your target, then rewrite.

Example `agent.system.main.specifics.md` for a data analyst:

```markdown
## Your role

You are a specialized data analysis agent.
Your expertise includes:
- Python data analysis (pandas, numpy, scipy)
- Data visualization (matplotlib, seaborn, plotly)
- Statistical modeling and hypothesis testing
- SQL queries and database analysis
- Data cleaning and preprocessing

## Process
1. Understand the data and the question
2. Choose appropriate tools and methods
3. Execute analysis with `code_execution_tool`
4. Visualize results when applicable
5. Provide clear interpretation of findings
```

### Secondary overrides (use only when needed)

| File | When to override | Shipped example |
|---|---|---|
| `agent.system.main.role.md` | Replace the base role framing wholesale (rare — most profiles layer via `specifics.md` instead) | `/a0/agents/agent0/prompts/agent.system.main.role.md` |
| `agent.system.main.communication.md` | Change reply format / communication style | `/a0/agents/developer/prompts/agent.system.main.communication.md`, `/a0/agents/researcher/prompts/...` |
| `agent.system.main.environment.md` | Describe a non-default runtime environment | `/a0/agents/hacker/prompts/agent.system.main.environment.md` (Kali/Docker) |
| `agent.system.tool.<name>.md` | Document a profile-specific tool (see Step 4) | `/a0/agents/_example/prompts/agent.system.tool.example_tool.md` |

> [!TIP]
> Only override what you actually need to change. Copying unchanged prompt files creates silent drift when the framework updates the originals — `specifics.md` is safe to own because its default is empty by design.

---

## Step 4 (optional): Profile-specific tools

Drop a Python tool class in `<PROFILE_ROOT>/<name>/tools/<tool_name>.py`:

```python
from helpers.tool import Tool, Response

class ExampleTool(Tool):
    async def execute(self, **kwargs):
        test_input = kwargs.get("test_input", "")
        return Response(
            message=f"Example tool executed with test_input: {test_input}",
            break_loop=False,
        )
```

Two important rules:

1. To make the tool visible in the system prompt, add `prompts/agent.system.tool.<tool_name>.md` describing its usage and JSON call schema. The prompt loader auto-includes every file matching `agent.system.tool.*.md`.
2. Placing a file with the same name as a core tool (e.g. `tools/response.py`) **replaces** the core tool for this profile only. See `/a0/agents/_example/tools/response.py` for a redefinition example.

---

## Step 5 (optional): Profile-specific extensions

Lifecycle hooks go in `<PROFILE_ROOT>/<name>/extensions/<hook_point>/_NN_<name>.py`. The `_NN_` prefix controls execution order.

Example — rename the agent at init (`/a0/agents/_example/extensions/agent_init/_10_example_extension.py`):

```python
from helpers.extension import Extension

class ExampleExtension(Extension):
    async def execute(self, **kwargs):
        self.agent.agent_name = "SuperAgent" + str(self.agent.number)
```

Available hook points mirror the framework's own `/a0/extensions/python/<point>/` directories — see `a0-development/SKILL.md` for the full list.

---

## Step 6: Test the new profile

1. The profile is picked up on next agent initialization — no restart of individual conversations needed, but a fresh agent/subordinate spawn is required.
2. From the superior agent, delegate to it via `call_subordinate` using the profile's **directory name** (not the title).
3. Verify:
   - Title appears correctly in the UI agent selector.
   - Role override (if any) takes effect in the new agent's system prompt.
   - Profile-specific tools are callable and their prompt files are included.

If the profile does not appear, check:
- Directory name matches the `^[a-z0-9_-]+$` pattern and is unique.
- `agent.yaml` parses as valid YAML.
- It is placed in one of the recognized search paths (see Step 0).

---

## Reference: Complete `_example` profile layout

```
/a0/agents/_example/
├── agent.yaml
├── prompts/
│   ├── agent.system.main.specifics.md     # role override
│   └── agent.system.tool.example_tool.md  # tool usage prompt
├── tools/
│   ├── example_tool.py                    # new tool
│   └── response.py                        # redefines core response tool
└── extensions/
    └── agent_init/
        └── _10_example_extension.py       # init-time hook
```

Copy this shape when in doubt — it demonstrates every customization surface a profile supports.

---

## Quick checklist

- [ ] Confirmed profile scope (user / plugin / project)
- [ ] Directory name is unique and matches allowed characters
- [ ] `agent.yaml` contains exactly `title`, `description`, `context`
- [ ] Prompt overrides only include files that actually change behavior
- [ ] Any new tool has a matching `agent.system.tool.<name>.md`
- [ ] Profile tested via `call_subordinate` in a fresh conversation
