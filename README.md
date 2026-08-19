# RKE2 Upgrade Troubleshooting Guide

This guide captures a practical troubleshooting flow for rolling RKE2 upgrades, especially when moving through newer Kubernetes versions where host-level requirements such as cgroup v2 can become upgrade blockers.

## Troubleshooting Tree

```mermaid
flowchart TD

    A["RKE2 Upgrade Completed<br/>Node/Cluster Not Healthy"] --> B{"Is rke2-server<br/>running?"}

    B -->|Yes| C["Check cluster health<br/>kubectl get nodes"]
    B -->|No| D["Check service logs<br/>journalctl -u rke2-server"]

    D --> E{"Kubelet failing?"}

    E -->|No| F["Investigate control-plane<br/>component errors"]
    E -->|Yes| G{"cgroup-related error?"}

    G -->|No| H["Investigate kubelet<br/>error directly"]
    G -->|Yes| I["Check cgroup version<br/>stat -fc %T /sys/fs/cgroup"]

    I --> J{"cgroup v2?"}

    J -->|No| K["Enable unified<br/>cgroup v2"]
    K --> L["Reboot host"]
    L --> M["Verify cgroup2fs"]
    M --> N["Restart rke2-server"]

    J -->|Yes| O{"cpu.weight or<br/>controller file missing?"}

    O -->|Yes| P["Inspect cgroup hierarchy<br/>and systemd slices"]
    P --> Q["Check available controllers<br/>cgroup.controllers"]
    Q --> R["Check enabled subtree controllers<br/>cgroup.subtree_control"]
    R --> S["Verify systemd is managing<br/>the unified hierarchy"]
    S --> N

    O -->|No| N

    N --> T{"RKE2 starts?"}

    T -->|No| D
    T -->|Yes| U["Check Kubernetes API"]

    U --> V["kubectl get nodes"]

    V --> W{"kubectl works?"}

    W -->|No| X["Test API endpoint<br/>and inspect server logs"]

    X --> Y{"Connection refused / 502?"}

    Y -->|Yes| Z["API server is not<br/>fully available"]
    Z --> D

    Y -->|No| AA{"401 Unauthorized?"}

    AA -->|Yes| AB["API server is reachable<br/>Authentication is the issue"]
    AB --> AC["Verify kubeconfig<br/>and credentials"]

    AA -->|No| AD["Investigate networking,<br/>TLS, or API errors"]

    W -->|Yes| AE["Check node state"]

    AE --> AF{"Node Ready?"}

    AF -->|No| AG["kubectl describe node"]
    AG --> AH["Inspect kubelet / runtime<br/>and node conditions"]

    AF -->|Yes| AI{"Node cordoned?"}

    AI -->|Yes| AJ["Uncordon node<br/>kubectl uncordon NODE"]
    AJ --> AN["Cluster healthy"]

    AI -->|No| AN["Cluster healthy"]
```

## Common Symptoms

| Symptom | First check | Likely issue | Recommended action |
|---|---|---|---|
| `rke2-server` fails after upgrade | `journalctl -u rke2-server -b` | Kubelet or control-plane startup failure | Find the first underlying kubelet/control-plane error |
| Kubelet says cgroup v1 is unsupported | `stat -fc %T /sys/fs/cgroup` | Host is still using cgroup v1 | Enable unified cgroup v2, reboot, and verify again |
| `cpu.weight` is missing | Inspect `/sys/fs/cgroup`, systemd slices, and `cgroup.controllers` | cgroup v2 hierarchy/controllers are not configured as expected | Verify systemd unified hierarchy and controller delegation |
| RKE2 starts but `kubectl` fails | Check API endpoint and RKE2 logs | API server may still be starting or failing | Diagnose the API server before troubleshooting workloads |
| `curl` returns `502` or connection refused | RKE2/API logs | API server is not accepting connections | Fix the control-plane startup issue |
| `curl` returns `401` | API endpoint | API server is reachable but authentication failed | Check kubeconfig, token, and certificates |
| Node is `Ready` but pods are not scheduling | `kubectl get nodes` | Node may still be cordoned after rolling maintenance | Run `kubectl uncordon <node>` |

## Recommended Troubleshooting Order

Use this order to avoid chasing downstream symptoms before the underlying platform is healthy:

**Host → RKE2 service → Kubelet → cgroup v2 → API server → node health → cordon state → healthy cluster**

## Useful Commands

```bash
# RKE2 service state
systemctl status rke2-server

# RKE2 server logs
journalctl -u rke2-server -b

# Check cgroup filesystem type
stat -fc %T /sys/fs/cgroup

# Available cgroup v2 controllers
cat /sys/fs/cgroup/cgroup.controllers

# Enabled subtree controllers
cat /sys/fs/cgroup/cgroup.subtree_control

# Kubernetes node state
kubectl get nodes -o wide

# Detailed node conditions
kubectl describe node <node>

# Restore scheduling after maintenance
kubectl uncordon <node>
```

## Notes

This guide intentionally focuses on reusable RKE2 and Kubernetes upgrade troubleshooting rather than environment-specific image or architecture mistakes.
