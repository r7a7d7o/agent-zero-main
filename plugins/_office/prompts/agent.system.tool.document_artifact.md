### document_artifact
create/open/read/edit reusable document artifacts in the Agent Zero canvas
formats: md docx xlsx pptx
default format: md
methods: create open read edit inspect export version_history restore_version status
common args: kind title format content path file_id
XLSX charts: use edit operation `create_chart` with `chart` object instead of code execution for embedded spreadsheet charts
chart types: line bar column pie area scatter stock ohlc candlestick
XLSX create/edit tabular content: CSV, TSV, Markdown tables, or rows arrays become real spreadsheet cells
ODT/ODS/ODP editing is intentionally unsupported in this migration
for nontrivial document artifact work, load skill `office-artifacts` first
