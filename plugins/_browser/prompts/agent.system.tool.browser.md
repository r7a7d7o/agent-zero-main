### browser
Rendered browser automation for pages that need interaction, JavaScript, forms, downloads, screenshots, or visual inspection.

Prefer `search_engine` or `document_query` for plain text research. Use the browser headlessly unless the user opens the Browser surface or asks for a visible browser.

The browser may run in Docker container mode or A0 CLI host-browser mode depending on settings. Container-mode paths resolve inside Agent Zero; host-mode paths resolve on the connected A0 CLI host.

For complex browser workflows, load skill `browser-tool`. For fragile forms, load skill `browser-forms`.

Actions: `open`, `list`, `state`, `set_active`, `navigate`, `back`, `forward`, `reload`, `content`, `detail`, `screenshot`, `click`, `hover`, `double_click`, `right_click`, `drag`, `type`, `submit`, `type_submit`, `scroll`, `evaluate`, `key_chord`, `mouse`, `wheel`, `keyboard`, `clipboard`, `set_viewport`, `select_option`, `set_checked`, `upload_file`, `multi`, `close`, `close_all`.

Common args: `action`, `browser_id`, `url`, `ref`, `target_ref`, `text`, `selector`, `selectors`, `script`, `modifiers`, `keys`, `key`, `include_content`, `focus_popup`, `event_type`, `x`, `y`, `to_x`, `to_y`, `delta_x`, `delta_y`, `button`, `quality`, `full_page`, `path`, `paths`, `value`, `values`, `checked`, `width`, `height`, `calls`.

Workflow:
- `open` creates a tab and returns id/state.
- `content` returns markdown with refs like `[link 3]`, `[button 6]`, `[input text 8]`.
- Interactions use refs from the latest `content` capture.
- `navigate` reuses an existing `browser_id` and is preferred for serial browsing.
- Screenshots are explicit only; call `vision_load` with the returned path before reasoning visually.
- Keep the tab set small; close pages after extracting what you need.

`multi` is only a browser action: use `tool_name: "browser"` with `tool_args.action: "multi"`. Never use `tool_name: "multi"`.

Example:
~~~json
{
  "tool_name": "browser",
  "tool_args": {
    "action": "open",
    "url": "https://example.com"
  }
}
~~~
