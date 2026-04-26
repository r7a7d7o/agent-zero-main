### document_artifact
create/open/inspect reusable Office artifacts in the Agent Zero canvas
use when producing substantial documents, spreadsheets, or presentations that should stay editable
do not dump long office-style artifacts only into chat when this tool is available

formats: docx xlsx pptx odt ods odp
actions: create open inspect export version_history restore_version status
common args: action kind title format content path file_id version_id

storage:
- generated files default to `/a0/usr/workdir/documents/`
- existing files must be under `/a0/usr/workdir`
- tool results include `canvas_surface: office`; open the Office canvas when collaborating on the artifact

examples:
~~~json
{
    "tool_name": "document_artifact",
    "tool_args": {
        "action": "create",
        "kind": "document",
        "title": "Project Brief",
        "format": "docx",
        "content": "Draft the brief here."
    }
}
~~~

~~~json
{
    "tool_name": "document_artifact",
    "tool_args": {
        "action": "open",
        "path": "/a0/usr/workdir/documents/Project Brief.docx"
    }
}
~~~
