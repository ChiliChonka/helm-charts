{{/*
Expand the name of the chart.
*/}}
{{- define "webserver.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "webserver.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "webserver.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "webserver.labels" -}}
helm.sh/chart: {{ include "webserver.chart" . }}
{{ include "webserver.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "webserver.selectorLabels" -}}
app.kubernetes.io/name: {{ include "webserver.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
redis alias
*/}}
{{- define "paperless.redis" -}}
{{- if .Values.paperlessVars.PAPERLESS_REDIS_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_REDIS_OVERWRITE }}
{{- else -}}
redis://{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_REDIS_HOST }}:{{ .Values.paperlessVars.PAPERLESS_REDIS_PORT }}
{{- end }}
{{- end }}

{{/*
database alias
*/}}
{{- define "paperless.dbhost" -}}
{{- if .Values.paperlessVars.PAPERLESS_DBHOST_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_DBHOST_OVERWRITE }}
{{- else -}}
{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_DBHOST }}
{{- end }}
{{- end }}

{{/*
tika alias
*/}}
{{- define "paperless.tikaEndpoint" -}}
{{- if .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_OVERWRITE }}
{{- else -}}
{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_PROTOCOL }}://{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT }}:{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_PORT }}
{{- end }}
{{- end }}

{{/*
gotenberg alias
*/}}
{{- define "paperless.tikaGotenbergEndpoint" -}}
{{- if .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_OVERWRITE }}
{{- else -}}
{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_PROTOCOL }}://{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT }}:{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_PORT }}
{{- end }}
{{- end }}


{{/*
redis readiness alias
*/}}
{{- define "paperless.redisReadinessCheck" -}}
{{- if .Values.paperlessVars.PAPERLESS_REDIS_READINESS_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_REDIS_READINESS_OVERWRITE }}
{{- else -}}
{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_REDIS_HOST }}:{{ .Values.paperlessVars.PAPERLESS_REDIS_PORT }}
{{- end }}
{{- end }}

{{/*
database readiness alias
*/}}
{{- define "paperless.dbhostReadinessCheck" -}}
{{- if .Values.paperlessVars.PAPERLESS_DBHOST_READINESS_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_DBHOST_READINESS_OVERWRITE }}
{{- else -}}
{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_DBHOST }}:{{ .Values.paperlessVars.PAPERLESS_DBPORT }}
{{- end }}
{{- end }}

{{/*
tika readiness alias
*/}}
{{- define "paperless.tikaEndpointReadinessCheck" -}}
{{- if .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_READINESS_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_READINESS_OVERWRITE }}
{{- else -}}
{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT }}:{{ .Values.paperlessVars.PAPERLESS_TIKA_ENDPOINT_PORT }}
{{- end }}
{{- end }}

{{/*
gotenberg readiness alias
*/}}
{{- define "paperless.tikaGotenbergEndpointReadinessCheck" -}}
{{- if .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_READINESS_OVERWRITE -}}
{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_READINESS_OVERWRITE }}
{{- else -}}
{{ .Release.Name }}-{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT }}:{{ .Values.paperlessVars.PAPERLESS_TIKA_GOTENBERG_ENDPOINT_PORT }}
{{- end }}
{{- end }}
