### document_artifact
create/open/read/edit reusable document artifacts in the Agent Zero canvas
formats: md docx xlsx pptx
default format: md
methods: create open read edit inspect export version_history restore_version status
common args: method action kind title format content path file_id
`method` is accepted as an alias for action when the tool_name has no suffix
tool results save or update artifacts only; they do not open the canvas automatically
created/updated artifacts are shown with explicit Download and Open in canvas message actions
XLSX charts: use edit operation `create_chart` with `chart` object instead of code execution for embedded spreadsheet charts
chart types: line bar column pie area scatter stock ohlc candlestick
XLSX create/edit tabular content: CSV, TSV, Markdown tables, or rows arrays become real spreadsheet cells
ODT/ODS/ODP editing is intentionally unsupported in this migration
for nontrivial document artifact work, load skill `office-artifacts` or the specific Markdown/Word/Excel/Presentation skill first
