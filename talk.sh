#!/bin/sh
# Talk to Reachy in plain English.
#
#   ./talk.sh                      interactive: type a sentence, press Enter
#   ./talk.sh "Reachy, look left"  one-shot
#
# Each sentence is handed to agent/minimal_agent.py running inside the OpenShell
# sandbox. The model picks move_head(directions), Python turns the words into a
# goto request, OpenShell allows or denies it, the Reachy daemon moves the head.
#
# Allow or deny motion live:
#   openshell policy set reachy-agent --policy openshell/policy-motion-enabled.yaml --wait
#   openshell policy set reachy-agent --policy openshell/policy-motion-disabled.yaml --wait
cd "$(dirname "$0")"

ask() {
  openshell sandbox exec --name reachy-agent --no-tty -- \
    /opt/venv/bin/python /sandbox/minimal_agent.py "$1" 2>&1 | grep -E '^\[(model chose|robot said|reply|busy|spoke)\]|Error'
}

if [ $# -gt 0 ]; then
  ask "$*"
  exit
fi

echo "Talking to Reachy. Try: look up / look left then right / nod yes. Ctrl-D to quit."
while printf '> ' && IFS= read -r line; do
  [ -n "$line" ] && ask "$line"
done
