{{- define "infracharts.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "infracharts.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "infracharts.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "infracharts.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "infracharts.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "infracharts.opensearchSetupName" -}}
{{- printf "%s-opensearch-setup" (include "infracharts.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
