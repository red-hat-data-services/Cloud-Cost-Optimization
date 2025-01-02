#!/bin/bash

set -e


## Helper functions used for formatting intput/output

group_json_by() {
  # usage: echo "{some json}" | group_json_by PARAM
  jq -r --arg PARAM "$1" 'group_by(.[$PARAM])[]| [{$PARAM:(.[0].[$PARAM]),values:[.[]|.name] }]  | .[] '
}

format_for_deletion() {
  # usage: echo "{some json}" | format_for_deletion PARAM
  group_json_by $1 |  jq -r --arg PARAM "$1" '"--" + $PARAM + "=" + .[$PARAM] + " " + (.values | join(" "))'
}

# gcloud config set project project-id

TWO_DAYS_AGO=$(python3 -c 'import datetime; print((datetime.datetime.now(datetime.UTC)-datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))')

if [ -z "$1" ]; then
  # get google dns managed zones
  ZONES=$(gcloud dns managed-zones list --filter="name ~ ^ci-op AND creationTime < $TWO_DAYS_AGO" --format json | jq -r '.[].name')

  # use the list of zones to get the total list of ci jobs.
  OLD_JOBS=$(echo "$ZONES" | sed 's/-private-zone$//')
else
  OLD_JOBS="$1"
fi
for OLD_JOB in $OLD_JOBS; do
  echo "Processing $OLD_JOB ..."

  echo "finding and deleting compute instance-related resources..."
  INSTANCES=$(gcloud compute instances list --filter "name ~ $OLD_JOB" --format json)
  echo "$INSTANCES" | format_for_deletion zone | xargs -r -L 1 gcloud compute instances delete --quiet

  DISKS=$(gcloud compute disks list --format json --filter "labels: $OLD_JOB" --format json)
  echo "$DISKS" | format_for_deletion zone | xargs -r -L 1 gcloud compute disks delete --quiet

  FWD_RULES=$(gcloud compute forwarding-rules list --filter "name ~ $OLD_JOB" --format json)
  echo "$FWD_RULES" | format_for_deletion region | xargs -r -L 1 gcloud compute forwarding-rules delete --quiet

  BACKEND_SVC=$(gcloud compute backend-services list --filter "name ~ $OLD_JOB" --format json)
  echo "$BACKEND_SVC" | format_for_deletion region | xargs -r gcloud compute backend-services delete --quiet

  INST_GROUPS=$(gcloud compute instance-groups list --filter "name ~ $OLD_JOB" --format json)
  echo "$INST_GROUPS" |  format_for_deletion zone | xargs -r -L 1 gcloud compute instance-groups unmanaged delete --quiet

  echo "finding and deleting networking-related resources..."
  FW_RULES=$(gcloud compute firewall-rules list --format json --filter "network ~ $OLD_JOB" | jq -r '.[].name')
  echo "$FW_RULES" | xargs -r gcloud compute firewall-rules delete --quiet

  ROUTERS=$(gcloud compute routers list --filter "name ~ $OLD_JOB" --format json) 
  echo "$ROUTERS" |  format_for_deletion region | xargs -r -L 1 gcloud compute routers delete --quiet

  FWD_RULES=$(gcloud compute forwarding-rules list --filter "name ~ $OLD_JOB" --format json)
  echo "$FWD_RULES" | format_for_deletion region | xargs -r -L 1 gcloud compute forwarding-rules delete --quiet

  ADDRS=$(gcloud compute addresses list --filter "name ~ $OLD_JOB" --format json) 
  echo "$ADDRS" | format_for_deletion region | xargs -r -L 1 gcloud compute addresses delete --quiet

  SUBNETS=$(gcloud compute networks subnets list --filter "name ~ $OLD_JOB" --format json)
  echo "$SUBNETS" | format_for_deletion region | xargs -r -L 1 gcloud compute networks subnets delete --quiet

  HEALTH_CHECKS=$(gcloud compute health-checks list --filter "name ~ $OLD_JOB" --format json)
  echo "$HEALTH_CHECKS" |  jq -r '.[].name' | xargs -r gcloud compute health-checks delete --quiet

  TARGET_POOLS=$(gcloud compute target-pools list --filter "name ~ $OLD_JOB" --format json)
  echo "$TARGET_POOLS" | format_for_deletion region  | xargs -r -L 1 gcloud compute target-pools delete --quiet

  NETWORKS=$(gcloud compute networks list --filter "name ~ $OLD_JOB" --format json | jq -r '.[].name')
  echo "$NETWORKS" | xargs -r gcloud compute networks delete --quiet

  echo "finding and deleting service accounts..."
  SVC_ACCTS=$(gcloud iam service-accounts list --filter "displayName ~ $OLD_JOB" --format json | jq -r '.[].email')
  echo "$SVC_ACCTS" | xargs -r -L 1 gcloud iam service-accounts delete --quiet

  echo "finding and deleting storage buckets..."
  BUCKETS=$(gcloud storage buckets list --filter "name ~ $OLD_JOB" --format json)
  OBJECTS=$(echo "$BUCKETS" | jq -r '.[].storage_url' | xargs -r gcloud storage objects list --format json)
  echo "$OBJECTS" | jq -r '.[].storage_url' | xargs -r gcloud storage rm --quiet
  echo "$BUCKETS" | jq -r '.[].storage_url' | xargs -r gcloud storage buckets delete --quiet

  # Delete zones last because we are leveraging it early to get the entire list of jobs. 
  # If we deleted them first and there's an error later, we wouldn't be able to get back the whole list of jobs
  echo "finding and deleting dns records and zones..."
  DNS_ZONE=$(gcloud dns managed-zones list --filter "name ~ $OLD_JOB" --format json | jq -r '.[].name')
  if [ -n "$DNS_ZONE" ] ; then
    DNS_RECORDS=$(gcloud dns record-sets list --zone "$DNS_ZONE" --format json)
    echo "$DNS_RECORDS" | jq -r '.[] | select(.type=="A") | .name' | xargs -r -L 1 gcloud dns record-sets delete --zone "$DNS_ZONE" --type A 
    gcloud dns managed-zones delete "$DNS_ZONE"
  fi
 

  echo "Cleanup of $OLD_JOB complete!"
  break
done

