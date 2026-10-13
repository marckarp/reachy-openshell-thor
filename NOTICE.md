# Attribution

This repository ports NVIDIA's OpenShell Research demo
"Policy-controlling Reachy Mini with OpenShell" to a Jetson AGX Thor with a USB Reachy Mini Lite.

Derived from, and documented against, material licensed under Apache License 2.0:

- NVIDIA OpenShell-Research, project `projects/use-case-examples/reachy-mini-openshell`
  (https://github.com/NVIDIA/OpenShell-Research), commit 6ea69fa. The policy files in
  `openshell/`, the sandbox Dockerfile idea, the fixed head-pose table and the tool design in
  `agent/minimal_agent.py` follow that project. Two of its documents are kept verbatim in
  `agent/docs/` (the dev note and the onboard setup guide).
- NVIDIA OpenShell provider profile `providers/nvidia.yaml`
  (https://github.com/NVIDIA/OpenShell), adapted as `openshell/reachy-nvidia.yaml`.

The Reachy Mini daemon, SDK and built-in sounds are Pollen Robotics' `reachy-mini` package
(installed from PyPI, not included here).

No credentials are stored in this repository. The NVIDIA API key lives only in the operator's
shell environment and, encrypted, inside the OpenShell gateway.
