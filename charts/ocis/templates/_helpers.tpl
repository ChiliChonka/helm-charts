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

{{/* CSP directives: values plus, with OIDC, the issuer's origin (scheme://host/) in connect-src. */}}
{{- define "ocis.csp" -}}
{{- $d := deepCopy .Values.csp.directives }}
{{- if .Values.oidc.enabled }}
{{- $u := urlParse .Values.oidc.issuer }}
{{- $origin := printf "%s://%s/" $u.scheme $u.host }}
{{- $_ := set $d "connect-src" (append (get $d "connect-src" | default list) $origin | uniq) }}
{{- end }}
{{- toYaml (dict "directives" $d) }}
{{- end }}
