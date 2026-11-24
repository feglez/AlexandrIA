#!/usr/bin/env bash
# End-to-end smoke test against the compose stack, with the real Nextcloud.
# Start the stack first, then run it from anywhere in the repository:
#
#   docker compose up -d --wait
#   tests/smoke/smoke.sh
#
# It reads the passwords from .env and needs curl, jq and docker compose.
# Every run submits files with new content, so it can run again on the same
# volumes.
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a
# shellcheck source=/dev/null
. ./.env
set +a

NEXTCLOUD_URL=${SMOKE_NEXTCLOUD_URL:-http://127.0.0.1:8080}
WATCHMAN_URL=${SMOKE_WATCHMAN_URL:-http://127.0.0.1:8081}
ALICE="alice:${DEMO_ALICE_PASSWORD}"
SERVICE="alexandria:${ALEXANDRIA_SERVICE_PASSWORD}"
DAV="$NEXTCLOUD_URL/remote.php/dav/files"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
body="$work/body"

step() { printf '\n== %s\n' "$*"; }
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    [[ -s $body ]] && cat "$body" >&2 && echo >&2
    exit 1
}

# expect STATUS CURL_ARGS...: run curl, keep the body in $body, check the status.
expect() {
    local expected=$1 status
    shift
    status=$(curl -sS -o "$body" -w '%{http_code}' "$@") || fail "curl $*"
    [[ $status == "$expected" ]] || fail "expected HTTP $expected, got $status from curl $*"
}

# field JQ_FILTER: print one value of the JSON body.
field() { jq -er "$1" "$body"; }

propfind() { expect 207 -X PROPFIND -H "Depth: 1" "$@"; }

run_id="$(date -u +%Y%m%d%H%M%S)-$RANDOM$RANDOM"

step "watchman lists the library collections"
expect 200 -u "$ALICE" "$WATCHMAN_URL/api/v1/collections"
field '.items[].name' | grep -qx Manuals || fail "Manuals is not a collection"

step "alice submits a file"
name="smoke-$run_id.md"
printf '# Smoke test\n\nRun %s.\n' "$run_id" > "$work/$name"
expect 202 -u "$ALICE" -F "file=@$work/$name" -F collection=Manuals -F "note=Smoke test" \
    "$WATCHMAN_URL/api/v1/submissions"
id=$(field .id)
echo "submission $id"
[[ $(field .status) == pending_review ]] || fail "status is not pending_review"
[[ $(field .mime_type) == text/markdown ]] || fail "wrong MIME type"
# observer serves no quota check yet: the check is unavailable (fail-safe).
[[ $(field '.checks[] | select(.name == "quota") | .result') == unavailable ]] ||
    fail "the quota check should be unavailable"

step "The file is in quarantine"
propfind -u "$SERVICE" "$DAV/alexandria/Quarantine/$id"
grep -q "$name" "$body" || fail "$name is not in Quarantine/$id"

step "The file is not visible in the library"
propfind -u "$ALICE" "$DAV/alice/Library/Manuals"
if grep -q "$name" "$body"; then fail "$name is visible in the library"; fi
expect 404 -X PROPFIND -H "Depth: 0" -u "$ALICE" "$DAV/alice/Quarantine"

step "The submission is stored with its history"
sql="SELECT s.status || ' ' || h.from_status || '>' || h.to_status
     FROM submissions s JOIN status_history h ON h.submission_id = s.id WHERE s.id = '$id'"
stored=$(docker compose exec -T postgres psql -U postgres -d watchman -tAc "$sql")
[[ $stored == "pending_review checking>pending_review" ]] || fail "database: '$stored'"

step "submission.created was published"
docker compose exec -T redis redis-cli -n 1 --raw XREVRANGE alexandria.events + - COUNT 50 |
    grep -F "\"$id\"" | grep -q '"submission.created"' || fail "no submission.created for $id"

step "A file whose content does not match its extension is rejected"
printf 'Not a PDF, run %s.\n' "$run_id" > "$work/fake.pdf"
expect 422 -u "$ALICE" -F "file=@$work/fake.pdf" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
[[ $(field .reason) == mime_mismatch ]] || fail "reason is not mime_mismatch"
field .submission_id > /dev/null

step "Submissions without credentials are rejected"
expect 401 -F "file=@$work/$name" -F collection=Manuals "$WATCHMAN_URL/api/v1/submissions"

# watchman has already used Nextcloud as alice and as the service account at
# this point, so a wrong password must not be accepted on their behalf.
step "A wrong password is rejected"
expect 401 -u "alice:wrong-$run_id" "$WATCHMAN_URL/api/v1/collections"

# Nextcloud counts failed logins per address. After about ten it refuses every
# login from watchman's address, for every member. Forget this one, so the test
# can run again on the same volumes.
watchman=$(docker compose ps -q watchman)
for ip in $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$watchman"); do
    docker compose exec -T -u www-data nextcloud php occ security:bruteforce:reset "$ip" > /dev/null
done

printf '\nSmoke test passed.\n'
