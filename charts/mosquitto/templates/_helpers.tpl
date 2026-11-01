{{- define "mosquitto.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default "mosquitto" .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Immutable on a Deployment, and a Service outside this chart may select on them. */}}
{{- define "mosquitto.selector" -}}
app.kubernetes.io/name: mosquitto
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "mosquitto.labels" -}}
{{ include "mosquitto.selector" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "mosquitto.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "mosquitto.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
