#!/bin/bash
# One unattended swisstopo batch: prepare -> upload -> check previews -> link stereo partners -> refresh list -> clean up.
# Files with any problem are set aside (listed in the log), never uploaded.
# Usage: bash run_batch.sh batch002 1000      Progress: <name>.log; final line starts with RESULT.
set -o pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
name=$1; count=$2; log=$name.log
step() { echo "== $(date +%H:%M) $*" >> "$log"; }

step prepare
python -u prepare_batch.py "$name.json" "$count" >> "$log" 2>&1 || { echo "RESULT failed in prepare" >> "$log"; exit 1; }

step upload
python -u upload.py "$name.json" --delay 6 2>&1 | grep --line-buffered -v -i password >> "$log" || { echo "RESULT failed in upload" >> "$log"; exit 1; }

step check previews
python -u check_thumbs.py "$name.json" >> "$log" 2>&1 || { echo "RESULT failed in preview check" >> "$log"; exit 1; }

step link partners
python -u link_partners.py 2>&1 | grep --line-buffered -v -i password >> "$log" || { echo "RESULT failed in partner links" >> "$log"; exit 1; }

step list
python -u make_manifest.py uploads.csv > /dev/null 2>> "$log" || { echo "RESULT failed in list" >> "$log"; exit 1; }

step clean up
python -c "
import json,os
for p in json.load(open('$name.json',encoding='utf-8')):
    if p.get('uploaded') and p.get('sdc_done'):
        for f in (p['stripped'], p['stripped'].replace('.stripped.tif','.tif')):
            if os.path.exists(f): os.remove(f)
"
summary=$(python -c "
import json;p=json.load(open('$name.json',encoding='utf-8'))
up=sum(1 for x in p if x.get('uploaded') and x.get('sdc_done'));held=[x['num'] for x in p if not x.get('uploaded')]
print(f'{up} of {len(p)} uploaded with structured data; set aside: {len(held)} {held[:20]}')")
echo "RESULT ok: $summary; $(grep -c 'transient server error' "$log") transient retries; $(grep '^previews:' "$log"); list has $(($(wc -l < uploads.csv)-1)) files" >> "$log"
