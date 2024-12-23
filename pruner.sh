
gcloud config set project $1

# get google dns managed zones

TWO_DAYS_AGO=$(python3 -c 'import datetime; print((datetime.datetime.now(datetime.UTC)-datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))')

# gcloud dns managed-zones list --filter="name ~ ^ci-op AND creationTime < $TWO_DAYS_AGO" --format json

OLD_JOBS=$(gcloud dns managed-zones list --filter="name ~ ^ci-op AND creationTime < $TWO_DAYS_AGO" --format json | jq -r '.[].name' | sed 's/-private-zone$//')

# dns records are attached to zones, so deleting the zones should take care of the records too

# roles/compute.viewer and roles/storage.admin

for OLD_JOB in $OLD_JOBS; do
  gcloud iam service-accounts list --filter "displayName ~ $OLD_JOB" --format json | jq -r '.[].displayName'
done
