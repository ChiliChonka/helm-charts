{{/* All objects are named after the release: Service <release>, claims <release>-config etc. */}}
{{- define "ocis.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ocis.selectorLabels" -}}
app.kubernetes.io/name: ocis
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "ocis.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "ocis.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Claim name of a volume: existingClaim or <release>-<volume>. */}}
{{- define "ocis.claimName" -}}
{{- $v := index .root.Values.persistence .volume }}
{{- $v.existingClaim | default (printf "%s-%s" (include "ocis.fullname" .root) .volume) }}
{{- end }}
