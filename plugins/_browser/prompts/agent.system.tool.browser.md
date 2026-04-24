### browser
direct Playwright browser control with visible WebUI viewer
use for web browsing, page inspection, forms, downloads, and browser-only tasks
state stays open per chat context
refs come from content as typed markers: [link 3], [button 6], [image 1], [input text 8]

actions: open list state navigate back forward reload content detail click type submit type_submit scroll evaluate close close_all
common args: action browser_id url ref text selector selectors script

workflow:
- open creates a new browser and returns id/state
- content returns readable page markdown with typed refs
- detail inspects one ref, including link/image/input/button metadata
- click/type/type_submit/submit/scroll use refs from latest content capture and return {action,state}
- navigate/back/forward/reload return fresh state
- list shows open browsers

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
