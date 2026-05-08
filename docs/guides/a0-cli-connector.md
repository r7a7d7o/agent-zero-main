# A0 CLI Connector

Agent Zero lives in Docker for a reason. That keeps it safer. The problem is that people see Docker and assume the agent can never really touch the code on their computer.

A0 CLI is the answer to that.

Agent Zero stays in Docker. A0 CLI installs on the host machine. That is what lets Agent Zero finally work on the real files on your real computer.

The same connector can also expose a host browser. Agent Zero still runs
server-side, but A0 CLI controls a real Chrome-family browser on the host and
routes it through Agent Zero's existing `browser` tool.

For now, use the install commands below.

## Quick Install

**macOS / Linux:**
```bash
curl -LsSf https://cli.agent-zero.ai/install.sh | sh
```

**Windows (PowerShell):**
```powershell
irm https://cli.agent-zero.ai/install.ps1 | iex
```

Run these on the host machine, not inside the Agent Zero container.

The installer uses `uv`, and `uv` will select or download a compatible Python if needed.

## Open it and start working

1. Make sure Agent Zero is already running.
2. Launch A0 CLI on the host machine:

```bash
a0
```

3. If Agent Zero is running on the same machine, A0 CLI will usually find it for you.
4. If Agent Zero is somewhere else, enter the exact web address or set `AGENT_ZERO_HOST` as env variable before launching `a0`.
5. Open or create a chat and confirm you can talk to Agent Zero from the host machine.

> [!NOTE]
> Current Agent Zero builds starting from v1.9 include the builtin connector support that A0 CLI expects. If you see a connector-specific `404`, update Agent Zero first.

## Host Browser

Use this when browser content should remain on the user's machine and you want
to pair it with local-model enforcement for host-browser content.

1. Keep A0 CLI connected to the Agent Zero chat.

2. If you want Agent Zero to use an already-open personal Chrome window, open
   `chrome://inspect/#remote-debugging` and click **Allow** for that browser
   instance. A0 CLI detects Chrome's local `DevToolsActivePort` file; status and
   profile checks do not connect to Chrome.

3. Optionally list or select a Chrome-family profile:

```bash
/browser profile
/browser profile chrome Default
/browser profile chrome-a0 Default
```

Chrome 136+ blocks Playwright remote debugging against the default personal
Chrome data directory. If Chrome's own Remote debugging consent path is not
available, choose the A0-controlled local profile (`chrome-a0 Default` for
Google Chrome). Cookies and site data remain in that separate browser profile on
the host, and the user may need to sign in there once.

4. In Agent Zero WebUI, open Browser plugin settings and choose one of:

- `container`: always use the Docker/server Playwright browser.
- `host_when_available`: use the A0 CLI host browser when the subscribed CLI can provide it, otherwise fall back to container.
- `host_required`: fail clearly unless a subscribed CLI can provide host browser control.

When host mode is selected in WebUI, the first Browser tool action asks the CLI
to enable and launch host browser control automatically. The slash commands are
still useful for diagnostics and manual override:

```bash
/browser status
/browser host on
/browser relaunch
```

5. If the selected Chrome profile is already open normally, A0 CLI reports
`relaunch_required`. Close that browser and retry the agent request or run
`/browser relaunch` manually.

The local-profile launch path uses Python Playwright against installed system
Chrome, Chromium, or Edge. The user-authorized Chrome remote debugging path uses
A0 CLI's built-in DevTools Protocol helper instead, so users do not need to
install Chrome DevTools MCP. A0 does not copy browser credentials, cookies, or
profile data out of the browser profile.

Host-browser page content and screenshots are controlled by the Browser
plugin's project-level policy:

- `enforce_local`: block content/screenshots unless the active chat model is local.
- `warn`: allow and include a warning in the tool result.
- `allow`: allow without warning.

## Give this to another agent

If another agent is helping with setup, do not paste a whole checklist. Paste one line:

```text
Set up the A0 CLI connector for Agent Zero on this machine using the a0-setup-cli Skill.
```

## Troubleshooting

- **Nothing appears locally:** Enter the Agent Zero web address manually or export `AGENT_ZERO_HOST`.
- **You tried to install from inside Docker:** A0 CLI belongs on the host machine. Agent Zero stays in Docker.
- **Function keys do nothing:** Some terminals and IDEs capture function keys. Use `Ctrl+P`.
- **Connector route returns `404`:** Update Agent Zero to a build with builtin connector support.
- **Host browser says Playwright is missing:** Install it in the A0 CLI environment with `python -m pip install playwright`.
- **Host browser waits for relaunch:** The selected Chrome-family profile is already locked by normal Chrome. Close that profile and run `/browser relaunch`.

## Related links

- [Quick Start](../quickstart.md)
- [Installation Guide](../setup/installation.md)
