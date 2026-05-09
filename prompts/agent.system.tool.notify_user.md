### notify_user
send an out-of-band notification without ending the current task
args: `message`, optional `title`, `detail`, `type`, `priority`, `timeout`
types: `info`, `success`, `warning`, `error`, `progress`
priority values: `20` high urgency, `10` normal urgency; omit for high
use for progress or alerts, not as the final answer
