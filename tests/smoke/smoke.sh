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
OBSERVER_URL=${SMOKE_OBSERVER_URL:-http://127.0.0.1:8083}
ALICE="alice:${DEMO_ALICE_PASSWORD}"
BOB="bob:${DEMO_BOB_PASSWORD}"
CAROL="carol:${DEMO_CAROL_PASSWORD}"
SERVICE="alexandria:${ALEXANDRIA_SERVICE_PASSWORD}"
DAV="$NEXTCLOUD_URL/remote.php/dav/files"

work=$(mktemp -d)
body="$work/body"
json=(-H "Content-Type: application/json")
# observer's container while the fail-safe step has it stopped: a failed run
# starts it again, so the next run finds the whole stack.
observer_stopped=
cleanup() {
    rm -rf "$work"
    if [[ -n $observer_stopped ]]; then docker start "$observer_stopped" > /dev/null || true; fi
}
trap cleanup EXIT

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

# query SQL [DATABASE]: run a query in watchman's database (or another one),
# print the result.
query() { docker compose exec -T postgres psql -U postgres -d "${2:-watchman}" -tAc "$1"; }

# decide ID approve|reject CURL_ARGS...: a curator decision on a submission.
decide() {
    local id=$1 action=$2
    shift 2
    expect "$@" -X POST "$WATCHMAN_URL/api/v1/submissions/$id/$action"
}

# history: the status history of the submission in $body, as "from>to:actor ...".
history() { field '[.history[] | "\(.from_status)>\(.to_status):\(.actor)"] | join(" ")'; }

# moved ID: wait until the mover has published the submission. Removing the
# empty quarantine folder is the last thing it does.
moved() {
    local code
    for _ in $(seq 30); do
        code=$(curl -sS -o /dev/null -w '%{http_code}' -X PROPFIND -H "Depth: 0" -u "$SERVICE" \
            "$DAV/alexandria/Quarantine/$1")
        [[ $code == 404 ]] && return
        sleep 1
    done
    fail "Quarantine/$1 is still there after 30 s"
}

# notified CREDENTIALS SUBJECT: print the message of the member's Nextcloud
# notification with this subject.
notified() {
    expect 200 -u "$1" -H "OCS-APIRequest: true" \
        "$NEXTCLOUD_URL/ocs/v2.php/apps/notifications/api/v2/notifications?format=json"
    jq -er --arg subject "$2" '.ocs.data[] | select(.subject == $subject) | .message' "$body"
}

# drained: wait until observer has handled every event in the stream (nothing
# left to read and nothing unacknowledged in its consumer group).
drained() {
    local left
    for _ in $(seq 60); do
        left=$(docker compose exec -T redis redis-cli -n 1 --raw XINFO GROUPS alexandria.events |
            paste - - | awk -F '\t' '
                $1 == "name" { group = $2 }
                group == "observer" && ($1 == "pending" || $1 == "lag") {
                    if ($2 !~ /^[0-9]+$/) bad = 1
                    left += $2; seen++
                }
                END { print (seen == 2 && !bad ? left : "unknown") }') || left=unknown
        [[ $left == 0 ]] && return
        sleep 1
    done
    fail "observer has not handled every event after 60 s ($left left)"
}

# usage CREDENTIALS: the member's usage from observer, in $body.
usage() { expect 200 -u "$1" "$OBSERVER_URL/api/v1/usage/me"; }

# quota CREDENTIALS MEMBER JSON: set the member's quota.
quota() { expect 200 -u "$1" -X PUT "${json[@]}" -d "$3" "$OBSERVER_URL/api/v1/quotas/$2"; }

# observer_up: start observer's container again and wait until it answers.
# Only the container: "docker compose start" would run bootstrap again.
observer_up() {
    docker start "$observer_stopped" > /dev/null
    for _ in $(seq 60); do
        if curl -fsS -o /dev/null "$OBSERVER_URL/healthz" 2> /dev/null; then
            observer_stopped=
            return
        fi
        sleep 1
    done
    fail "observer does not answer 60 s after it started"
}

run_id="$(date -u +%Y%m%d%H%M%S)-$RANDOM$RANDOM"
# Every run adds files and submissions for alice and bob, so they get a quota
# larger than the default one: the test can run many times on the same volumes.
QUOTA='{"max_contribution_bytes": 10737418240, "max_submissions_per_month": 1000}'

step "watchman lists the library collections"
expect 200 -u "$ALICE" "$WATCHMAN_URL/api/v1/collections"
field '.items[].name' | grep -qx Manuals || fail "Manuals is not a collection"

step "carol gives alice and bob a quota of their own"
for member in alice bob; do
    quota "$CAROL" "$member" "$QUOTA"
    [[ $(field .updated_by) == carol ]] || fail "updated_by is not carol"
done

step "Members can read their own quota, but not set it"
expect 200 -u "$ALICE" "$OBSERVER_URL/api/v1/quotas/alice"
[[ $(field .max_submissions_per_month) == 1000 ]] || fail "alice's quota is not the new one"
expect 403 -u "$ALICE" "$OBSERVER_URL/api/v1/quotas/bob"
expect 403 -u "$ALICE" -X PUT "${json[@]}" -d "$QUOTA" "$OBSERVER_URL/api/v1/quotas/alice"
expect 422 -u "$CAROL" -X PUT "${json[@]}" -d '{"max_contribution_bytes": -1}' \
    "$OBSERVER_URL/api/v1/quotas/alice"

# Earlier runs on the same volumes count too: the checks below compare with
# these figures.
step "observer reports alice's usage"
drained
usage "$ALICE"
[[ $(field .user) == alice ]] || fail "user is not alice"
[[ $(field .period) == "$(date -u +%Y-%m)" ]] || fail "period is not this month"
[[ $(field .submissions.limit) == 1000 ]] || fail "the submission limit is not alice's quota"
[[ $(field .library_contribution.quota_bytes) == 10737418240 ]] ||
    fail "the contribution quota is not alice's quota"
contributed=$(field .library_contribution.used_bytes)
submitted=$(field .submissions.this_period)
storage=$(field .personal_storage.used_bytes)
expect 200 -u "$ALICE" -H "OCS-APIRequest: true" \
    "$NEXTCLOUD_URL/ocs/v2.php/cloud/users/alice?format=json"
[[ $(field .ocs.data.quota.used) == "$storage" ]] ||
    fail "personal storage is $storage bytes, Nextcloud says $(field .ocs.data.quota.used)"

step "alice submits a file"
name="smoke-$run_id.md"
printf '# Smoke test\n\nRun %s.\n' "$run_id" > "$work/$name"
expect 202 -u "$ALICE" -F "file=@$work/$name" -F collection=Manuals -F "note=Smoke test" \
    "$WATCHMAN_URL/api/v1/submissions"
id=$(field .id)
echo "submission $id"
[[ $(field .status) == pending_review ]] || fail "status is not pending_review"
[[ $(field .mime_type) == text/markdown ]] || fail "wrong MIME type"
[[ $(field '.checks[] | select(.name == "quota") | .result') == pass ]] ||
    fail "the quota check did not pass"

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

step "alice cannot approve her own submission"
decide "$id" approve 403 -u "$ALICE"

step "carol sees it among the pending submissions"
expect 200 -u "$CAROL" "$WATCHMAN_URL/api/v1/submissions?status=pending_review&limit=100"
[[ $(field ".items[] | select(.id == \"$id\") | .submitted_by") == alice ]] ||
    fail "$id is not in the pending list"
expect 200 -u "$CAROL" "$WATCHMAN_URL/api/v1/submissions?mine=true&limit=100"
if field '.items[].id' | grep -qx "$id"; then fail "$id is in carol's own submissions"; fi

step "alice follows her submission, which bob cannot see"
expect 200 -u "$ALICE" "$WATCHMAN_URL/api/v1/submissions?mine=true&limit=100"
[[ $(field ".items[] | select(.id == \"$id\") | .status") == pending_review ]] ||
    fail "$id is not in alice's submissions"
expect 200 -u "$ALICE" "$WATCHMAN_URL/api/v1/submissions/$id"
[[ $(history) == "checking>pending_review:alice" ]] || fail "history: '$(history)'"
[[ $(field '.checks | length') == 5 ]] || fail "the detail has no check results"
expect 404 -u "$BOB" "$WATCHMAN_URL/api/v1/submissions/$id"

step "carol approves it"
decide "$id" approve 200 -u "$CAROL"
[[ $(field .status) == approved ]] || fail "status is not approved"

# The mover publishes it in the background.
step "watchman publishes it"
moved "$id"

step "alice sees the file in the library"
propfind -u "$ALICE" "$DAV/alice/Library/Manuals"
grep -q "$name" "$body" || fail "$name is not in alice's Library/Manuals"

step "The publication is stored with its history"
stored=$(query "SELECT string_agg(from_status || '>' || to_status || ':' || actor, ' ' ORDER BY id)
                FROM status_history WHERE submission_id = '$id'")
[[ $stored == "checking>pending_review:alice pending_review>approved:carol approved>published:watchman" ]] ||
    fail "history: '$stored'"
stored=$(query "SELECT s.status || ' ' || f.path FROM submissions s
                JOIN library_files f ON f.submission_id = s.id WHERE s.id = '$id'")
[[ $stored == "published Library/Manuals/$name" ]] || fail "database: '$stored'"

# The mover sends the notification before it deletes the quarantine folder.
step "alice is notified in Nextcloud"
message=$(notified "$ALICE" "Your file $name is in the library")
[[ $message == "carol approved it, and it is now at Library/Manuals/$name." ]] ||
    fail "notification: '$message'"

step "file.published was published"
docker compose exec -T redis redis-cli -n 1 --raw XREVRANGE alexandria.events + - COUNT 50 |
    grep -F "\"$id\"" | grep -q '"file.published"' || fail "no file.published for $id"

step "observer adds the published file to alice's contribution"
drained
usage "$ALICE"
size=$(wc -c < "$work/$name")
[[ $(field .library_contribution.used_bytes) == $((contributed + size)) ]] ||
    fail "contribution is not $((contributed + size)) bytes"

step "The same content again is a duplicate"
cp "$work/$name" "$work/copy-$name"
expect 422 -u "$ALICE" -F "file=@$work/copy-$name" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
[[ $(field .reason) == duplicate ]] || fail "reason is not duplicate"
[[ $(field .existing_path) == "Library/Manuals/$name" ]] || fail "wrong existing_path"

step "carol rejects a submission, with a reason"
other="smoke-$run_id-rejected.txt"
printf 'To be rejected, run %s.\n' "$run_id" > "$work/$other"
expect 202 -u "$ALICE" -F "file=@$work/$other" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
rejected=$(field .id)
decide "$rejected" reject 422 -u "$CAROL" "${json[@]}" -d '{"reason": " "}'
decide "$rejected" reject 200 -u "$CAROL" "${json[@]}" -d '{"reason": "Smoke test"}'
[[ $(field .status) == rejected ]] || fail "status is not rejected"
expect 404 -X PROPFIND -H "Depth: 0" -u "$SERVICE" "$DAV/alexandria/Quarantine/$rejected"
stored=$(query "SELECT to_status || ':' || actor || ':' || reason FROM status_history
                WHERE submission_id = '$rejected' ORDER BY id DESC LIMIT 1")
[[ $stored == "rejected:carol:Smoke test" ]] || fail "history: '$stored'"
message=$(notified "$ALICE" "Your file $other was rejected")
[[ $message == "carol rejected it. Reason: Smoke test" ]] || fail "notification: '$message'"

step "A decided submission cannot be decided again"
decide "$rejected" approve 409 -u "$CAROL"
[[ $(field .current_status) == rejected ]] || fail "current_status is not rejected"

step "alice withdraws a pending submission"
third="smoke-$run_id-withdrawn.txt"
printf 'To be withdrawn, run %s.\n' "$run_id" > "$work/$third"
expect 202 -u "$ALICE" -F "file=@$work/$third" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
withdrawn=$(field .id)
expect 404 -u "$BOB" -X DELETE "$WATCHMAN_URL/api/v1/submissions/$withdrawn"
expect 403 -u "$CAROL" -X DELETE "$WATCHMAN_URL/api/v1/submissions/$withdrawn"
expect 204 -u "$ALICE" -X DELETE "$WATCHMAN_URL/api/v1/submissions/$withdrawn"
expect 404 -X PROPFIND -H "Depth: 0" -u "$SERVICE" "$DAV/alexandria/Quarantine/$withdrawn"
expect 200 -u "$ALICE" "$WATCHMAN_URL/api/v1/submissions/$withdrawn"
[[ $(field .status) == withdrawn ]] || fail "status is not withdrawn"
[[ $(history) == "checking>pending_review:alice pending_review>withdrawn:alice" ]] ||
    fail "history: '$(history)'"

step "Only pending submissions can be withdrawn"
for sub in "$withdrawn:withdrawn" "$rejected:rejected" "$id:published"; do
    expect 409 -u "$ALICE" -X DELETE "$WATCHMAN_URL/api/v1/submissions/${sub%%:*}"
    [[ $(field .current_status) == "${sub#*:}" ]] || fail "current_status is not ${sub#*:}"
done

POLICIES="$WATCHMAN_URL/api/v1/policies"

step "carol reads the moderation policy"
expect 200 -u "$CAROL" "$POLICIES"
version=$(field .version)
field '.policy.auto_approve.groups[]' | grep -qx trusted || fail "trusted is not an auto-approve group"
jq -c .policy "$body" > "$work/policy.json"

step "Members cannot read or change the policy"
expect 403 -u "$ALICE" "$POLICIES"
expect 403 -u "$ALICE" -X PUT "${json[@]}" -d "@$work/policy.json" "$POLICIES"

step "An invalid policy is refused with the errors"
expect 422 -u "$CAROL" -X PUT "${json[@]}" -d '{"max_size_bytes": 0}' "$POLICIES"
errors=$(field '.errors[].loc | join(".")')
grep -qx body.max_size_bytes <<< "$errors" || fail "no error for max_size_bytes"
grep -qx body.auto_approve <<< "$errors" || fail "no error for the missing auto_approve"

# Saving the same document keeps the policy as it was, so the test can run again.
step "carol saves the policy, which becomes a new version"
expect 200 -u "$CAROL" -X PUT "${json[@]}" -d "@$work/policy.json" "$POLICIES"
new_version=$((version + 1))
[[ $(field .version) == "$new_version" ]] || fail "version is not $new_version"
[[ $(field .created_by) == carol ]] || fail "created_by is not carol"
stored=$(query "SELECT string_agg(version || ':' || created_by, ' ' ORDER BY version)
                FROM policies WHERE version >= $version")
[[ $stored == "$version:"*" $new_version:carol" ]] || fail "versions: '$stored'"

step "bob is in the trusted group"
expect 200 -u "$BOB" -H "OCS-APIRequest: true" "$NEXTCLOUD_URL/ocs/v2.php/cloud/user?format=json"
field '.ocs.data.groups[]' | grep -qx trusted || fail "bob is not in the trusted group"

step "bob is trusted, so a file from bob is published without a curator"
trusted="smoke-$run_id-trusted.txt"
printf 'From a trusted member, run %s.\n' "$run_id" > "$work/$trusted"
expect 202 -u "$BOB" -F "file=@$work/$trusted" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
auto=$(field .id)
[[ $(field .status) == approved ]] || fail "status is not approved"
[[ $(field '.checks[] | select(.name == "quota") | .result') == pass ]] ||
    fail "the quota check did not pass"
moved "$auto"
expect 200 -u "$BOB" "$WATCHMAN_URL/api/v1/submissions/$auto"
[[ $(field .status) == published ]] || fail "status is not published"
[[ $(history) == "checking>approved:watchman approved>published:watchman" ]] ||
    fail "history: '$(history)'"
[[ $(field '.history[0].reason') == auto_approve ]] || fail "the reason is not auto_approve"
stored=$(query "SELECT policy_version FROM submissions WHERE id = '$auto'")
[[ $stored == "$new_version" ]] || fail "policy version: '$stored'"
propfind -u "$BOB" "$DAV/bob/Library/Manuals"
grep -q "$trusted" "$body" || fail "$trusted is not in bob's Library/Manuals"
message=$(notified "$BOB" "Your file $trusted is in the library")
[[ $message == "It was approved automatically, and it is now at Library/Manuals/$trusted." ]] ||
    fail "notification: '$message'"

# watchman does not depend on observer: if the quota check gets no answer, the
# file waits for a curator, even from a trusted member (fail-safe).
step "While observer is down, bob's next file waits for a curator"
observer_stopped=$(docker compose ps -q observer)
docker stop "$observer_stopped" > /dev/null
waiting="smoke-$run_id-waiting.txt"
printf 'Submitted while observer is down, run %s.\n' "$run_id" > "$work/$waiting"
expect 202 -u "$BOB" -F "file=@$work/$waiting" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
[[ $(field .status) == pending_review ]] || fail "status is not pending_review"
[[ $(field '.checks[] | select(.name == "quota") | .result') == unavailable ]] ||
    fail "the quota check should be unavailable"
waiting_id=$(field .id)
observer_up
# Withdrawn, so pending submissions do not pile up over many runs.
expect 204 -u "$BOB" -X DELETE "$WATCHMAN_URL/api/v1/submissions/$waiting_id"

step "A file whose content does not match its extension is rejected"
printf 'Not a PDF, run %s.\n' "$run_id" > "$work/fake.pdf"
expect 422 -u "$ALICE" -F "file=@$work/fake.pdf" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
[[ $(field .reason) == mime_mismatch ]] || fail "reason is not mime_mismatch"
field .submission_id > /dev/null

# Accepted: the published, the rejected and the withdrawn file. The duplicate
# and the file whose content does not match were rejected by the checks.
step "observer counts alice's accepted submissions"
drained
usage "$ALICE"
[[ $(field .submissions.this_period) == $((submitted + 3)) ]] ||
    fail "submissions this month are not $((submitted + 3))"

step "Members cannot see the usage of others"
expect 403 -u "$BOB" "$OBSERVER_URL/api/v1/usage"
expect 401 "$OBSERVER_URL/api/v1/usage/me"

# carol submits nothing else, so this quota does not get in the way of the
# other steps if a run stops half-way.
step "carol gets a quota of 0 submissions, and the next file from carol is rejected"
quota "$CAROL" carol '{"max_contribution_bytes": 10737418240, "max_submissions_per_month": 0}'
over="smoke-$run_id-over-quota.txt"
printf 'Over the quota, run %s.\n' "$run_id" > "$work/$over"
expect 422 -u "$CAROL" -F "file=@$work/$over" -F collection=Manuals \
    "$WATCHMAN_URL/api/v1/submissions"
[[ $(field .reason) == quota_exceeded ]] || fail "reason is not quota_exceeded"
[[ $(field '.checks[] | select(.name == "quota") | .detail') == *" and 0 submissions left this month." ]] ||
    fail "the quota check does not say what is left"
quota "$CAROL" carol "$QUOTA"

step "observer's quota check is not reachable from outside"
expect 404 -X POST "${json[@]}" -d '{"user": "alice", "size_bytes": 1}' \
    "$OBSERVER_URL/internal/quota-checks"
docker inspect -f '{{json .HostConfig.PortBindings}}' "$(docker compose ps -q observer)" |
    jq -e 'has("8000/tcp") and (has("8001/tcp") | not)' > /dev/null ||
    fail "observer's internal port is published"

step "observer keeps snapshots of the members' storage"
stored=$(query "SELECT used_bytes FROM storage_snapshots WHERE member = 'alice'
                ORDER BY taken_at DESC LIMIT 1" observer)
[[ $stored == "$storage" ]] || fail "snapshot of alice: '$stored' bytes, Nextcloud says $storage"

step "carol gets the cost-split report of last month"
last_month=$(date -u -d "$(date -u +%Y-%m-15) -1 month" +%Y-%m)
expect 200 -u "$CAROL" "$OBSERVER_URL/api/v1/reports/$last_month"
[[ $(field .period) == "$last_month" ]] || fail "period is not $last_month"
[[ $(field .split_rule) == proportional_to_storage ]] || fail "wrong split rule"
jq -e --argjson cost "${MONTHLY_COST_EUR:-5.40}" '.monthly_cost_eur == $cost' "$body" > /dev/null ||
    fail "the monthly cost is not ${MONTHLY_COST_EUR:-5.40}"
jq -e '(.members | length) == 0 or
    ((.members | map(.amount_eur) | add) * 100 | round) == (.monthly_cost_eur * 100 | round)' \
    "$body" > /dev/null || fail "the amounts do not add up to the monthly cost"
expect 409 -u "$CAROL" "$OBSERVER_URL/api/v1/reports/$(date -u +%Y-%m)"
expect 403 -u "$ALICE" "$OBSERVER_URL/api/v1/reports/$last_month"

step "carol sees the usage of every member"
expect 200 -u "$CAROL" "$OBSERVER_URL/api/v1/usage?limit=100"
[[ $(field .period) == "$(date -u +%Y-%m)" ]] || fail "period is not this month"
[[ $(field '.items[] | select(.user == "alice") | .library_contribution.used_bytes') == \
    $((contributed + size)) ]] || fail "alice's contribution is not $((contributed + size)) bytes"
field '.items[] | select(.user == "bob") | .submissions.this_period' > /dev/null ||
    fail "bob is not in the list"
expect 422 -u "$CAROL" "$OBSERVER_URL/api/v1/usage?period=2026-13"

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
