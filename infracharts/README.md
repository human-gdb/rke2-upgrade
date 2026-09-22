# Infra Charts

This umbrella chart deploys OpenSearch and OpenSearch Dashboards with Keycloak OIDC authentication.

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
operations in `opensearchSetup.operations`, waits for Dashboards, and imports
saved objects when an export file is packaged with the chart.

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
