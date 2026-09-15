# Infra Charts

This umbrella chart deploys OpenSearch and OpenSearch Dashboards with Keycloak OIDC authentication.

## Required secrets

Create these secrets in the target namespace before installing the chart:

- `opensearch-initial-admin` with key `password`
- `opensearch-dashboards-account` with keys `username`, `password`, and `cookie`
- `opensearch-dashboards-oidc` with key `client-secret`

The Dashboards account password must match the internal OpenSearch account configured for the Dashboards server, normally `kibanaserver`.

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
