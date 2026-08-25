# Upgrading RKE2 in an Air-Gapped Environment

This runbook describes a manual, rolling upgrade of an existing Linux RKE2 cluster when the nodes cannot reach the internet.

The procedure assumes:

- The cluster uses RKE2's bundled containerd.
- You can download artifacts on a connected machine and transfer them through an approved path.
- You will use the same RKE2 installation method already used on the nodes.
- The cluster is upgraded one Kubernetes minor version at a time. For example, upgrade `v1.25.x` to a compatible `v1.26.x` release before moving to `v1.27.x`.

Replace every value in angle brackets before running a command. Commands that change cluster state should be run during a maintenance window.

## 1. Choose and validate the target version

1. Select an exact target release, including the RKE2 patch suffix. Do not upgrade to an unpinned channel in a disconnected environment.

   ```bash
   TARGET_VERSION="v1.36.1+rke2r2" # replace with the approved target release
   ```

2. Confirm that the target release is supported by the installed Rancher version, operating system, kernel, CNI, ingress controller, CSI/CPI components, and any operators in the cluster.

3. Confirm that the upgrade does not skip a Kubernetes minor version. The RKE2 upgrade documentation warns that the Kubernetes version-skew policy applies and that the upgrade process will not protect against an unsupported minor-version jump.

4. Record the current state on an RKE2 server or an administration workstation with a working kubeconfig:

   ```bash
   rke2 --version
   kubectl version --short 2>/dev/null || kubectl version
   kubectl get nodes -o wide
   kubectl get pods -A -o wide
   kubectl get events -A --sort-by=.lastTimestamp | tail -n 50
   ```

5. On an RKE2 server, use the RKE2-provided kubeconfig and client tools when possible. This avoids accidentally using an old `kubectl` from `/usr/local/bin`:

   ```bash
   export PATH="/var/lib/rancher/rke2/bin:${PATH}"
   export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
   type -a kubectl
   kubectl version --client
   ```

   The kubeconfig generated on a server normally points to `127.0.0.1`. From an external workstation, use a copy whose `server:` address points to the cluster's stable server address or load balancer.

6. Identify the CNI and installation method. Do not assume that the default RKE2 image archive contains your CNI:

   ```bash
   kubectl get pods -A | grep -Ei 'cilium|canal|calico|flannel|weave'
   command -v rke2
   rpm -qa | grep -E '^rke2-(server|agent|common|selinux)' || true
   systemctl cat rke2-server 2>/dev/null || systemctl cat rke2-agent
   ```

   `rke2-images.linux-amd64.tar.zst` contains Canal images. A cluster using Cilium needs the Cilium image archive, or the core archive plus the Cilium archive. This distinction is important: importing the wrong architecture or the wrong CNI archive can result in `FailedCreatePodSandBox` errors or a missing CNI socket after the upgrade.

## 2. Check host prerequisites

Perform these checks on every server and agent before transferring artifacts:

```bash
uname -m
stat -fc %T /sys/fs/cgroup
ip route show default
df -h /var/lib/rancher/rke2
free -h
systemctl is-enabled rke2-server 2>/dev/null || systemctl is-enabled rke2-agent
systemctl is-active rke2-server 2>/dev/null || systemctl is-active rke2-agent
```

For `x86_64` nodes, use `amd64` artifacts. For `aarch64` nodes, use `arm64` artifacts. If the cluster contains multiple architectures, prepare and transfer the matching archive set for each architecture.

Newer RKE2 releases may require unified cgroup v2. A healthy result for the cgroup check is normally `cgroup2fs`. If the host still uses cgroup v1, resolve that operating-system requirement before changing the RKE2 binary. After changing boot parameters, reboot and verify the result again.

Every node must have a usable default route before RKE2 starts. This is required even in an isolated network because RKE2 uses it to detect the node's primary IP and for kube-proxy ClusterIP routing.

If SELinux is enabled on an air-gapped RPM-based node, stage `rke2-selinux` and its required OS dependencies before the upgrade. Do not attempt to obtain them from an internet repository on the offline node.

## 3. Create a recovery point

### Embedded etcd

RKE2's etcd snapshots apply to clusters using the embedded etcd datastore. Create an on-demand snapshot before transferring or installing the new version:

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
sudo rke2 etcd-snapshot save --name "pre-upgrade-${STAMP}"
sudo rke2 etcd-snapshot ls
```

Copy the snapshot to approved protected storage and record its exact path. Also protect the server token; it is required to decrypt confidential bootstrap data during a restore:

```bash
sudo cp /var/lib/rancher/rke2/server/token "/root/rke2-server-token-pre-upgrade-${STAMP}"
sudo tar -C / -czf "/root/rke2-config-pre-upgrade-${STAMP}.tgz" \
  etc/rancher/rke2
```

The snapshot and token contain sensitive cluster data and private keys. Store them with the same protections as the cluster's secrets. A snapshot does not back up external databases, application data in PVCs, or other systems outside the Kubernetes datastore.

### External datastore

If `datastore-endpoint` is configured, back up the external PostgreSQL, MySQL, or etcd datastore using that datastore's supported backup procedure. RKE2's embedded-etcd snapshot commands are not a substitute for an external-database backup.

Before proceeding, verify that the backup can be read and that you have the RKE2 server token and the node configuration needed to restore the cluster.

## 4. Download artifacts on a connected machine

Use a connected machine with the same target architecture as the offline nodes, or set `ARCH` explicitly to the nodes' architecture. Keep the filenames from the release unchanged.

```bash
TARGET_VERSION="v1.36.1+rke2r2" # replace with the approved target release
RELEASE_TAG="${TARGET_VERSION//+/%2B}"
ARCH="amd64"                     # use arm64 for aarch64 nodes
ARTIFACT_DIR="${PWD}/rke2-artifacts-${TARGET_VERSION}"
BASE_URL="https://github.com/rancher/rke2/releases/download/${RELEASE_TAG}"

mkdir -p "${ARTIFACT_DIR}"
cd "${ARTIFACT_DIR}"

curl -fL "${BASE_URL}/rke2.linux-${ARCH}.tar.gz" \
  -o "rke2.linux-${ARCH}.tar.gz"
curl -fL "${BASE_URL}/sha256sum-${ARCH}.txt" \
  -o "sha256sum-${ARCH}.txt"
curl -fL "https://get.rke2.io" -o install.sh
```

Download the image archive matching the CNI:

```bash
# For Canal:
curl -fL "${BASE_URL}/rke2-images.linux-${ARCH}.tar.zst" \
  -o "rke2-images.linux-${ARCH}.tar.zst"
```

```bash
# For Cilium:
curl -fL "${BASE_URL}/rke2-images-core.linux-${ARCH}.tar.zst" \
  -o "rke2-images-core.linux-${ARCH}.tar.zst"
curl -fL "${BASE_URL}/rke2-images-cilium.linux-${ARCH}.tar.zst" \
  -o "rke2-images-cilium.linux-${ARCH}.tar.zst"
```

If the cluster uses vSphere CPI/CSI, GPU operators, a private ingress controller, or other add-ons with images not included in the selected RKE2 archive, stage those images separately through the approved registry or image-transfer process.

Verify the release artifacts before they cross the air gap:

```bash
chmod 0755 install.sh
sha256sum -c "sha256sum-${ARCH}.txt" --ignore-missing
sha256sum ./*
```

Review `install.sh` according to your organization's change-control process. Do not run its `curl` commands on an offline node. Transfer the complete artifact directory, including `install.sh`, the RKE2 binary archive, the checksum file, and the selected image archive(s), to every node that will be upgraded.

## 5. Stage the target images on each node

On each node, validate the transferred files again and place the target image archive(s) in RKE2's image directory:

```bash
ARTIFACT_DIR=/root/rke2-artifacts
ARCH=amd64 # use the node's architecture

cd "${ARTIFACT_DIR}"
sha256sum -c "sha256sum-${ARCH}.txt" --ignore-missing

sudo mkdir -p /var/lib/rancher/rke2/agent/images
```

For Canal:

```bash
sudo cp "${ARTIFACT_DIR}/rke2-images.linux-${ARCH}.tar.zst" \
  /var/lib/rancher/rke2/agent/images/
```

For Cilium:

```bash
sudo cp "${ARTIFACT_DIR}/rke2-images-core.linux-${ARCH}.tar.zst" \
  "${ARTIFACT_DIR}/rke2-images-cilium.linux-${ARCH}.tar.zst" \
  /var/lib/rancher/rke2/agent/images/
```

RKE2 imports image tarballs placed in `/var/lib/rancher/rke2/agent/images/`. After the target archives have been verified, remove stale RKE2 image archives from that directory so they are not repeatedly imported. Keep a protected copy of the previous-version artifacts if you may need an offline rollback.

If the node has an image-import cache file and the new archive was copied over an existing filename, force a re-import before restarting RKE2:

```bash
sudo touch /var/lib/rancher/rke2/agent/images/<TARGET-IMAGE-ARCHIVE>
# Or clear the contents of the cache file if your release uses it:
sudo sh -c ': > /var/lib/rancher/rke2/agent/images/.cache.json'
```

Do not use a glob as the source of a destructive command. Inspect the image directory first and remove only the old, explicitly identified archives.

### Optional: use a private registry

For clusters with an internal OCI registry, load the target image archive on a connected staging host, push the images to the internal registry, and configure `/etc/rancher/rke2/registries.yaml` on every node. Preserve the existing registry configuration and credentials; do not replace it with a sample file without merging the required settings.

If using `system-default-registry`, set it to the registry host and optional port only, such as `registry.example.com:5000`. For a fully disconnected cluster, configure mirrors and credentials for every upstream registry referenced by the workloads and packaged components. Test a pull from an offline node before the maintenance window.

### If the nodes use RPM installation

Do not switch an RPM-installed node to the tarball installer during an upgrade. Stage the target `rke2-server` or `rke2-agent` RPM, `rke2-common`, `rke2-selinux` when applicable, and all OS dependencies. Install them from the local directory with the distribution's offline package manager, for example:

```bash
sudo dnf --disablerepo='*' install ./rke2-common-<VERSION>.rpm ./rke2-server-<VERSION>.rpm
sudo systemctl restart rke2-server
```

Use `rke2-agent-<VERSION>.rpm` and restart `rke2-agent` on an agent. Package filenames and dependency requirements vary by distribution; validate the complete RPM set on a connected staging host first.

## 6. Upgrade server nodes one at a time

RKE2 requires the server nodes to be upgraded before agents. With embedded etcd, never stop two etcd server members at the same time. A single-server cluster will have control-plane downtime while its server is restarted.

Repeat the following sequence for each server node:

1. Identify the node and, if it runs schedulable workloads, cordon and drain it. Do not add `--force` automatically; resolve PodDisruptionBudget or unmanaged-pod blocks deliberately.

   ```bash
   NODE=<server-node-name>
   kubectl cordon "${NODE}"
   kubectl drain "${NODE}" \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --timeout=15m
   ```

2. On that server, stop RKE2, run the local installer against the staged artifacts, and start the service:

   ```bash
   sudo systemctl stop rke2-server
   sudo env INSTALL_RKE2_ARTIFACT_PATH=/root/rke2-artifacts \
     sh /root/rke2-artifacts/install.sh
   sudo systemctl start rke2-server
   ```

   The server install must retain the existing `/etc/rancher/rke2/config.yaml`, token, registry configuration, TLS settings, CNI settings, and any custom data directory. The offline installer updates the RKE2 installation and systemd unit; it does not replace your cluster configuration unless you overwrite it yourself.

3. Verify the local service and binary before moving to another server:

   ```bash
   sudo systemctl is-active rke2-server
   rke2 --version
   sudo journalctl -u rke2-server -b --no-pager -n 100
   ```

4. From a working administration session, wait for the node to return to `Ready` and confirm that system components are healthy:

   ```bash
   kubectl wait --for=condition=Ready "node/${NODE}" --timeout=15m
   kubectl get node "${NODE}" -o wide
   kubectl get pods -A -o wide
   ```

5. Uncordon the server only after it is healthy:

   ```bash
   kubectl uncordon "${NODE}"
   ```

6. Allow the cluster to settle, confirm etcd and the API server are healthy, and then repeat the sequence for the next server.

## 7. Upgrade agent nodes one at a time

Repeat this sequence for each agent node after all servers are healthy:

1. Cordon and drain the agent:

   ```bash
   NODE=<agent-node-name>
   kubectl cordon "${NODE}"
   kubectl drain "${NODE}" \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --timeout=15m
   ```

2. On that agent, install the target version from the local artifact directory. Set `INSTALL_RKE2_TYPE=agent`; otherwise the installer may install the server service.

   ```bash
   sudo systemctl stop rke2-agent
   sudo env INSTALL_RKE2_ARTIFACT_PATH=/root/rke2-artifacts \
     INSTALL_RKE2_TYPE=agent \
     sh /root/rke2-artifacts/install.sh
   sudo systemctl start rke2-agent
   ```

3. Verify the service and node:

   ```bash
   sudo systemctl is-active rke2-agent
   rke2 --version
   sudo journalctl -u rke2-agent -b --no-pager -n 100
   kubectl wait --for=condition=Ready "node/${NODE}" --timeout=15m
   kubectl get node "${NODE}" -o wide
   ```

4. Uncordon the agent after it is healthy:

   ```bash
   kubectl uncordon "${NODE}"
   ```

## 8. Verify the completed upgrade

Run the following checks after every node has been upgraded:

```bash
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get daemonsets -A
kubectl get events -A --sort-by=.lastTimestamp | tail -n 100
```

Look specifically for `NotReady`, `ImagePullBackOff`, `ErrImagePull`, `CrashLoopBackOff`, `CreateContainerError`, and `FailedCreatePodSandBox`.

Confirm that all nodes report the expected kubelet minor version:

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.nodeInfo.kubeletVersion}{"\n"}{end}'
```

On each node, also confirm the RKE2 service and version:

```bash
sudo systemctl is-active rke2-server 2>/dev/null || sudo systemctl is-active rke2-agent
rke2 --version
```

For a Cilium cluster, confirm that Cilium pods are running and that new pods can obtain networking. For an existing NGINX ingress deployment, verify its controller pods and an application route. RKE2 does not automatically migrate an existing ingress controller just because the cluster is upgraded; review the target release notes before moving to a release where the default for new clusters changes.

Finally, exercise representative workloads, persistent volumes, ingress routes, DNS, service-to-service traffic, monitoring, and any external integrations before closing the maintenance window.

## 9. Troubleshooting common air-gap failures

| Symptom | First checks | Likely cause or action |
|---|---|---|
| `rke2-server` or `rke2-agent` will not start | `systemctl status` and `journalctl -u rke2-server -b` or `journalctl -u rke2-agent -b` | Fix the first startup error. Later pod errors are often downstream symptoms. |
| `kubectl` returns connection refused or `502` | Check the RKE2 service, API-server logs, and port `6443` | The API server may still be starting or the control plane may have failed. Do not replace kubeconfig until reachability and authentication are separated. |
| `kubectl` returns `401 Unauthorized` | Check the kubeconfig path, server address, client certificate, and token | The API is reachable; this is an authentication or kubeconfig problem. |
| `cgroup v1 is unsupported` | `stat -fc %T /sys/fs/cgroup` | Enable and verify unified cgroup v2 on the host before retrying the RKE2 upgrade. |
| `cpu.weight` or another cgroup file is missing | `cat /sys/fs/cgroup/cgroup.controllers` and `cat /sys/fs/cgroup/cgroup.subtree_control` | Inspect systemd's unified hierarchy and controller delegation. |
| `FailedCreatePodSandBox`, missing CNI socket, or CNI pods fail | Check node architecture and CNI-specific images in the image directory | The archive may be for the wrong architecture or may contain Canal while the cluster uses Cilium. Re-stage the correct archive and force a re-import. |
| `ImagePullBackOff` in an air-gapped cluster | Inspect the image name, `registries.yaml`, and local containerd images | The image is absent from the node and internal registry, or the mirror/TLS/authentication configuration is incorrect. |
| The node is `Ready` but workloads do not schedule | `kubectl get node` and `kubectl describe node` | The node may still be cordoned after maintenance; run `kubectl uncordon <node>` after confirming it is healthy. |
| Image archives seem to be ignored | Check `/var/lib/rancher/rke2/agent/images` and `.cache.json` | Touch the target archive or clear the cache contents, then restart RKE2. |

Keep the old artifacts until the new cluster has passed application verification. Do not delete the only copy of the previous binary, image archives, snapshot, token, or configuration backup.

## 10. Rollback outline

Rollback is a recovery operation, not a normal version change. For embedded etcd, a reliable rollback requires both the previous RKE2 artifacts and a valid pre-upgrade etcd snapshot. Downgrading the binary alone does not restore the previous Kubernetes datastore state.

1. Stop or drain workloads according to the outage plan. If the API is available, drain nodes gracefully. If it is not available, stop RKE2 on the nodes and use `rke2-killall.sh` only with an understanding of the process and data-loss risks.
2. Stage the previous RKE2 binary and matching image archives on every node using the same air-gap procedure.
3. Install the previous server and agent version offline on every node, but do not start all members at once.
4. For embedded etcd, stop RKE2 on all server nodes. On the first server, restore the pre-upgrade snapshot:

   ```bash
   sudo rke2 server \
     --cluster-reset \
     --cluster-reset-restore-path=<PATH-TO-PRE-UPGRADE-SNAPSHOT>
   ```

   Use the original server token when restoring to a different host. The restore overwrites the etcd datastore on the node, so verify the snapshot path and integrity first.

5. Start RKE2 on the restored first server. After it is healthy, back up and remove the RKE2 server database directory on each other etcd server, then start those servers so they rejoin the restored cluster. Use the exact data directory configured for the cluster; the default is `/var/lib/rancher/rke2/server/db`.
6. Start the agents, verify node and workload health, and keep the cluster cordoned until validation is complete.
7. For an external datastore, restore the external database backup first, then start the previous RKE2 version on the nodes.

Consult the official rollback procedure for the complete single-server or multi-server sequence before executing a restore.

## References

- [RKE2 Air-Gap Install](https://docs.rke2.io/install/airgap)
- [RKE2 Manual Upgrades](https://docs.rke2.io/upgrades/manual)
- [RKE2 Backup and Restore](https://docs.rke2.io/datastore/backup_restore)
- [RKE2 Rolling Back](https://docs.rke2.io/upgrades/roll-back)
- [RKE2 Import Images](https://docs.rke2.io/add-ons/import-images)
