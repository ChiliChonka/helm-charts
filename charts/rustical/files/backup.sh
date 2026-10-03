#!/bin/sh
# Nightly backup of RustiCal (busybox sh + sqlite3, e.g. alpine/sqlite):
#   <OUT>/aktuell/db.sqlite3          consistent copy of the database (.backup, works while running)
#   <OUT>/aktuell/export/<principal>/<calendar>.ics    one importable file per calendar
#   <OUT>/aktuell/export/<principal>/<addressbook>.vcf one importable file per address book
#   <OUT>/aktuell/INHALT.txt          which file is which calendar (display names, counts)
# Only live calendars and objects are exported; deleted ones stay in the database copy.
# The export needs no RustiCal: any calendar/contacts app imports .ics/.vcf.
# Exit code 1 if the copy is damaged or the export does not contain every object.
set -eu

DB="${DB:-/data/db.sqlite3}"
OUT="${OUT:-/backup}"
TAB="$(printf '\t')"
new="$OUT/.neu"

[ -f "$DB" ] || { echo "FEHLER: $DB fehlt"; exit 1; }
rm -rf "$new"
mkdir -p "$new/export" "$new/.obj"

# Online backup API: consistent even with RustiCal writing (WAL mode). Needs write access to the
# source directory for the -shm file, so the data volume is mounted read-write.
sqlite3 "$DB" ".timeout 30000" ".backup '$new/db.sqlite3'"
check="$(sqlite3 "$new/db.sqlite3" "PRAGMA integrity_check;")"
[ "$check" = ok ] || { echo "FEHLER: integrity_check: $check"; exit 1; }

q() { sqlite3 -batch -noheader -separator "$TAB" "$new/db.sqlite3" "$1"; }
sql() { printf '%s' "$1" | sed "s/'/''/g"; }            # SQL string literal content
fname() { printf '%s' "$1" | tr '/\\\000' '___'; }      # one path component

# Merge single iCalendar objects into one VCALENDAR: components (VEVENT, VTODO, VJOURNAL with
# their VALARMs) as they are, each VTIMEZONE once. Folded lines are kept. Output uses CRLF.
COMBINE='
BEGIN { printf "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//rustical-backup//EN\r\n" }
{ sub(/\r$/, "") }
/^BEGIN:VTIMEZONE$/ { intz = 1; buf = ""; tzid = "" }
intz {
  buf = buf $0 "\r\n"
  if ($0 ~ /^TZID[:;]/) { tzid = $0; sub(/^TZID[^:]*:/, "", tzid) }
  if ($0 == "END:VTIMEZONE") { if (!(tzid in seen)) { seen[tzid] = 1; printf "%s", buf }; intz = 0 }
  next
}
/^BEGIN:(VEVENT|VTODO|VJOURNAL)$/ { incomp = 1 }
incomp { printf "%s\r\n", $0; if ($0 ~ /^END:(VEVENT|VTODO|VJOURNAL)$/) incomp = 0; next }
END { printf "END:VCALENDAR\r\n" }
'

exported=0
index="$new/INHALT.txt"
printf 'RustiCal-Sicherung vom %s (UTC)\n\n' "$(date -u '+%Y-%m-%d %H:%M')" > "$index"

# Calendars ----------------------------------------------------------------------------------
q "SELECT principal, id, coalesce(displayname, '') FROM calendars
   WHERE deleted_at IS NULL AND subscription_url IS NULL ORDER BY principal, id;" > "$new/.cals"
while IFS="$TAB" read -r p c name; do
  dir="$new/export/$(fname "$p")"
  mkdir -p "$dir"
  rm -f "$new"/.obj/*
  q "SELECT writefile('$new/.obj/' || rowid || '.ics', ics) FROM calendarobjects
     WHERE principal = '$(sql "$p")' AND cal_id = '$(sql "$c")' AND deleted_at IS NULL;" >/dev/null
  n="$(find "$new/.obj" -type f | wc -l)"
  if [ "$n" -gt 0 ]; then
    awk "$COMBINE" "$new"/.obj/*.ics > "$dir/$(fname "$c").ics"
  else
    awk "$COMBINE" /dev/null > "$dir/$(fname "$c").ics"
  fi
  exported=$((exported + n))
  printf 'Kalender     %-50s %-30s %5d Einträge\n' "$(fname "$p")/$(fname "$c").ics" "$name" "$n" >> "$index"
done < "$new/.cals"

# Address books ------------------------------------------------------------------------------
q "SELECT principal, id, coalesce(displayname, '') FROM addressbooks
   WHERE deleted_at IS NULL ORDER BY principal, id;" > "$new/.abs"
while IFS="$TAB" read -r p a name; do
  dir="$new/export/$(fname "$p")"
  mkdir -p "$dir"
  rm -f "$new"/.obj/*
  q "SELECT writefile('$new/.obj/' || rowid || '.vcf', vcf) FROM addressobjects
     WHERE principal = '$(sql "$p")' AND addressbook_id = '$(sql "$a")' AND deleted_at IS NULL;" >/dev/null
  n="$(find "$new/.obj" -type f | wc -l)"
  if [ "$n" -gt 0 ]; then
    # vCards may simply follow each other; normalise line endings to CRLF.
    awk '{ sub(/\r$/, ""); printf "%s\r\n", $0 }' "$new"/.obj/*.vcf > "$dir/$(fname "$a").vcf"
  else
    : > "$dir/$(fname "$a").vcf"
  fi
  exported=$((exported + n))
  printf 'Adressbuch   %-50s %-30s %5d Kontakte\n' "$(fname "$p")/$(fname "$a").vcf" "$name" "$n" >> "$index"
done < "$new/.abs"

# Every live object must be in the export.
expected="$(q "SELECT
  (SELECT count(*) FROM calendarobjects o JOIN calendars c ON c.principal = o.principal AND c.id = o.cal_id
     WHERE o.deleted_at IS NULL AND c.deleted_at IS NULL AND c.subscription_url IS NULL)
+ (SELECT count(*) FROM addressobjects o JOIN addressbooks a ON a.principal = o.principal AND a.id = o.addressbook_id
     WHERE o.deleted_at IS NULL AND a.deleted_at IS NULL);")"
if [ "$exported" -ne "$expected" ]; then
  echo "FEHLER: $exported Objekte exportiert, in der Datenbank sind $expected"
  exit 1
fi
rm -rf "$new/.obj" "$new/.cals" "$new/.abs"

# Swap in the new state only when everything above worked.
rm -rf "$OUT/.alt"
if [ -d "$OUT/aktuell" ]; then mv "$OUT/aktuell" "$OUT/.alt"; fi
mv "$new" "$OUT/aktuell"
rm -rf "$OUT/.alt"
echo "ok: $(wc -l < "$OUT/aktuell/INHALT.txt" | awk '{print $1 - 2}') Sammlungen, $exported Objekte, Datenbank-Kopie geprüft"
