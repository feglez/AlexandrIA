#!/bin/sh
# Post-installation hook of the official Nextcloud image: it runs once, right
# after the installation, as www-data and before the web server starts.
# It only does what needs occ. The bootstrap container creates the library
# folders, the share and the sub-admin rights over HTTP once Nextcloud is up.
set -eu

occ() {
    php /var/www/html/occ "$@"
}

occ app:enable notifications
occ app:enable webhook_listeners
occ background:cron

# New accounts start with an empty home folder instead of the sample files.
occ config:system:set skeletondirectory --value=''

for group in members curators trusted; do
    occ group:add "$group"
done

# Service account: owns the Library and Quarantine folders. It needs admin
# rights to send notifications and register webhooks.
env OC_PASS="$ALEXANDRIA_SERVICE_PASSWORD" php /var/www/html/occ user:add \
    --password-from-env --display-name "AlexandrIA" --group admin alexandria

# observer reads the storage of the members (sub-admin of "members", set by
# the bootstrap container).
env OC_PASS="$OBSERVER_ACCOUNT_PASSWORD" php /var/www/html/occ user:add \
    --password-from-env --display-name "AlexandrIA observer" observer

# Demo household: alice and bob are members, carol is also a curator.
env OC_PASS="$DEMO_ALICE_PASSWORD" php /var/www/html/occ user:add \
    --password-from-env --display-name "Alice" --group members alice
env OC_PASS="$DEMO_BOB_PASSWORD" php /var/www/html/occ user:add \
    --password-from-env --display-name "Bob" --group members bob
env OC_PASS="$DEMO_CAROL_PASSWORD" php /var/www/html/occ user:add \
    --password-from-env --display-name "Carol" --group members --group curators carol
