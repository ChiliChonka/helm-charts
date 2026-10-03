{{/* All objects are named after the release: Deployment/Service <release>, claim <release>-data. */}}
{{- define "rustical.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "rustical.selectorLabels" -}}
app.kubernetes.io/name: rustical
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "rustical.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "rustical.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "rustical.dataClaim" -}}
{{- .Values.persistence.existingClaim | default (printf "%s-data" (include "rustical.fullname" .)) }}
{{- end }}

{{- define "rustical.backupClaim" -}}
{{- .Values.backup.existingClaim | default (printf "%s-backup" (include "rustical.fullname" .)) }}
{{- end }}
