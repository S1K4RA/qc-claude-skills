---
type: regex
target: last_message
pattern: '(unset QC_TOKEN|env -u QC_TOKEN|QC_TOKEN=(\s|''''|"")|--token-env\s+(?!QC_TOKEN\b)\w+)[\s\S]*probe\.py|probe\.py[^\n]*--token-env\s+(?!QC_TOKEN\b)\w+'
weight: 2
---
