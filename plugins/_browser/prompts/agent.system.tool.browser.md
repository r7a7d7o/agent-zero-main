### browser
direct Playwright browser control with optional visible WebUI viewer
use for web browsing, page inspection, forms, downloads, and browser-only tasks
state stays open per chat context
refs come from content as typed markers: [link 3], [button 6], [image 1], [input text 8]

Browser tool actions must not open the right canvas automatically. Use the tool headlessly unless the user opens the Browser canvas or explicitly asks for a visible browser view; if the Browser canvas is already open, it may reflect the active page.

actions: open list state set_active navigate back forward reload content detail click type submit type_submit scroll evaluate key_chord mouse multi close close_all
common args: action browser_id url ref text selector selectors script modifiers keys include_content focus_popup event_type x y button calls

workflow:
- open creates a new browser and returns id/state
- content returns readable page markdown with typed refs
- detail inspects one ref, including link/image/input/button metadata
- click/type/type_submit/submit/scroll use refs from latest content capture and return {action,state}
- navigate/back/forward/reload return fresh state
- list shows open browsers; pass include_content: true for one-call bulk read

modifier clicks:
- click accepts modifiers like ["Control"], ["Shift"], ["Alt"], ["Meta"]
- ctrl/meta-click opens link in new tab in background (Chrome rule)
- override with focus_popup: true (focus follows new tab) or false (always background)
- the new tab id is reported in action.opened_browser_ids; list shows all tabs

popup awareness:
- tabs opened by site (window.open, target=_blank, ctrl-click) auto-register
- list returns every tab; last_interacted_browser_id tracks current focus

background work (do not steal focus):
- operations on a non-active tab (read, click, type, evaluate, etc.) target that tab WITHOUT moving focus
- last_interacted_browser_id (and the WebUI viewer that follows it) only changes on:
  - open (new tab created)
  - explicit set_active action
  - action on the already-active tab
  - chrome popup-focus rule (plain click on target=_blank → follow; ctrl-click → stay)
- to switch focus deliberately: {"action":"set_active","browser_id":N}

key_chord:
- presses keys in order, releases in reverse; safe across exceptions
- example: {"action":"key_chord","keys":["Control","a"]} selects all

multi (parallel batch):
- run many actions concurrently across tabs in one tool call
- pass calls: array of action objects (each has its own action+args)
- different browser_ids run in parallel; same browser_id runs in submit order
- returns array of {"ok":true,"result":...} or {"ok":false,"error":"..."} matching input order
- ideal for: scrape N tabs at once, fan-out reads, parallel evaluate
- avoid mutating same tab twice in one batch unless serial order is intended

examples:
~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "open",
        "url": "https://example.com"
    }
}
~~~

~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "content",
        "browser_id": 1
    }
}
~~~

~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "click",
        "browser_id": 1,
        "ref": 3
    }
}
~~~

~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "click",
        "browser_id": 1,
        "ref": 3,
        "modifiers": ["Control"]
    }
}
~~~

~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "key_chord",
        "browser_id": 1,
        "keys": ["Control", "a"]
    }
}
~~~

~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "list",
        "include_content": true
    }
}
~~~

~~~json
{
    "tool_name": "browser",
    "tool_args": {
        "action": "multi",
        "calls": [
            {"action": "content", "browser_id": 1},
            {"action": "content", "browser_id": 2},
            {"action": "evaluate", "browser_id": 3, "script": "document.title"}
        ]
    }
}
~~~
