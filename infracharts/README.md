# Infra Charts

This umbrella chart deploys OpenSearch and OpenSearch Dashboards with Keycloak OIDC authentication.

## Generic job runner

Supply a `jobs` map to create one regular Kubernetes Job per child key.
Each child has its own configuration and is rendered only when `run: true`.
The default is `run: false`. For jobs selected to run, `image`, `command`,
and `args` are required, with command and args supplied as nonempty lists.
Omit `run` or set it to `false` to skip a job.

Job names come from the child keys without a release prefix. Camel-case keys
are converted to Kubernetes-compatible kebab case: `opensearchSetup` becomes
`opensearch-setup`. Invalid names and duplicate names after conversion fail
rendering. Names must be unique within the namespace, including across releases.

```yaml
jobs:
  opensearchSetup:
    run: true
    image: registry.example.com/job-runner:1.0.0
    command: ["python"]
    args: ["/app/main.py"]
    envFrom:
      - configMapRef:
          name: runner-config
      - secretRef:
          name: runner-secrets
    env:
      - name: LOG_LEVEL
        value: "INFO"
      - name: PASSWORD
        valueFrom:
          secretKeyRef:
            name: credentials
            key: password
  cleanup:
    run: true
    image: registry.example.com/job-runner:1.0.0
    command: ["python"]
    args: ["/app/cleanup.py"]
    backoffLimit: 0
```

Per-job defaults are `run: false`, `backoffLimit: 2`,
`imagePullPolicy: IfNotPresent`, and `restartPolicy: Never`. A backoff limit
of zero is supported. Optional `serviceAccountName`, `imagePullSecrets`, and
`resources` use native Kubernetes structures. When omitted, Kubernetes uses
the namespace's default ServiceAccount and any applicable admission defaults;
the template specifies no resources or additional environment variables.

Secrets and ConfigMaps must exist in the release namespace. Explicit `env`
entries override matching `envFrom` variables. The runner is not a Helm hook
and does not rerun automatically on upgrades. Delete the completed Job before
rerunning it or upgrading its immutable Pod template (image, command, or env).

## Required secrets

Create these secrets in the target namespace before installing the chart:

- `opensearch-initial-admin` with key `password`
- `opensearch-dashboards-account` with keys `username`, `password`, and `cookie`
- `opensearch-dashboards-oidc` with key `client-secret`

The Dashboards account password must match the internal OpenSearch account configured for the Dashboards server, normally `kibanaserver`. Dashboards exposes both OIDC and basic authentication so that browsers can continue redirecting to Keycloak while the bootstrap Job can authenticate to the saved-objects API.

## Keycloak client

Create a confidential client named `opensearch-dashboards` and allow this redirect URI:

```text
https://logs.example.com/opensearch-dashboards/auth/openid/login
```

Update the Keycloak issuer, OpenSearch service settings, hostname, TLS secret, and image registry in `values.yaml` for the target cluster.

## Install

```bash
helm repo add opensearch https://opensearch-project.github.io/helm-charts/
helm repo update
helm dependency update ./infracharts
helm upgrade --install infracharts ./infracharts \
  --namespace opensearch \
  --create-namespace
```

The NGINX Ingress intentionally does not use `rewrite-target`; it forwards `/opensearch-dashboards` unchanged because Dashboards is configured with `server.rewriteBasePath: true`.

## OpenSearch bootstrap job

When `opensearchSetup.enabled` is true, Helm creates a ConfigMap containing
`scripts/opensearch_bootstrap.py` through `.Files.Get` and runs it as a
post-install/post-upgrade hook Job. The Job waits for OpenSearch, applies the
operations in `scripts/opensearch-setup.yaml`, waits for Dashboards, and imports
saved objects when an export file is packaged with the chart.

Edit the `operations` list in `scripts/opensearch-setup.yaml` to configure the
REST requests. Helm reads this file with `.Files.Get`, parses the YAML, and
mounts the operations as `opensearch-setup.json` for the Python script. This
keeps the runtime dependency-free for air-gapped deployments. Set
`operations: []` to skip cluster changes.

Create the bootstrap credentials before installing the chart:

```bash
kubectl -n opensearch create secret generic opensearch-bootstrap-auth \
  --from-literal=username=admin \
  --from-literal=password='REPLACE_ME'
```

To import saved objects, create
`saved-objects/saved-objects.ndjson` beneath the chart directory. The file must
be an NDJSON export from OpenSearch Dashboards with referenced objects included.
Override `opensearchSetup.savedObjectsFile` when using another chart-relative
path.

For an air-gapped installation, mirror the configured Python image and override
`opensearchSetup.image.repository`. Configure `opensearchSetup.tls.caSecretName`
and `caKey` for the cluster CA, or set the Secret name to an empty string when
the CA is already trusted by the image.
