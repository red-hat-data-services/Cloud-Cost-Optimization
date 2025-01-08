#!/bin/bash

set -e

help() {
cat << EOF
usage: ./pruner.sh [-j] [-p] [-s] [JOB_ID_1 JOB_ID_2 ...]
  -j, --show-old-jobs - Print old job names and exit
  -p, --project - Specify google cloud project id
  -s, --service-account - Specify google cloud service account (need to specify a key file as well)
  -k, --key-file - Specify key file for google cloud service account
  JOB_IDs - delete resources for specific OpenShift CI job IDs only
EOF
}

SHOW_OLD_JOBS=
PROJECT=
SVC_ACCT=
KEY_FILE=

while [ "$#" -gt 0 ]; do
  key="$1"
  case $key in 
    --help | -h)
      help
      exit
      ;;
    --show-old-jobs | -j)
      SHOW_OLD_JOBS=true
      shift
      ;;
    --project | -p)
      PROJECT="$2"
      if [ -z "$PROJECT" ]; then
        echo "please specify a project id after $1"
        help
        exit 1
      fi
      shift 2
      ;;
    --service-account | -s)
      SVC_ACCT="$2"
      if [ -z "$SVC_ACCT" ]; then
        echo "please specify a service account after $1"
        help
        exit 1
      fi
      shift 2
      ;;
    --key-file | -k)
      KEY_FILE="$2"
      if [ -z "$KEY_FILE" ]; then
        echo "please specify a key file path after $1"
        help
        exit 1
      fi
      shift 2
      ;;
    -*)
      echo "unrecognized argument $1"
      help
      exit 1
      ;;
    *)
      break
      ;;
  esac
done
if [ -n "$SVC_ACCT" ]; then
  if [ -z "$KEY_FILE" ]; then
    echo "Please specify a key file for the service account $SVC_ACCT"
    help
    exit 1
  fi
  gcloud auth activate-service-account "$SVC_ACCT" --key-file "$KEY_FILE" --no-user-output-enabled
fi

if [ -n "$PROJECT" ]; then
  gcloud config set project "$PROJECT" --no-user-output-enabled --quiet
fi


DATE_FORMAT="%Y-%m-%dT%H:%M:%SZ"
NUM_DAYS_BACK=2
CUTOFF_DATE=$([ "$(uname)" = Linux ] && date --date="$NUM_DAYS_BACK days ago" +"$DATE_FORMAT" || date -v "-${NUM_DAYS_BACK}d" +"$DATE_FORMAT")

if [ -z "$1" ]; then
  # get google dns managed zones
  ZONES=$(gcloud dns managed-zones list --filter="name ~ ^ci-op AND creationTime < $CUTOFF_DATE" --format json | jq -r '.[].name')

  # use the list of zones to get the total list of ci jobs.
  OLD_JOBS=$(echo "$ZONES" | sed 's/-private-zone$//')
else
  OLD_JOBS="$@"
fi

if [ -n "$SHOW_OLD_JOBS" ]; then
  for job in $OLD_JOBS; do
    echo "$job"
  done
  exit 0
fi

## Helper functions used for formatting intput/output

# example INPUT for both helper functions:
#  [{
#    "region":"A",
#    "name":"apple"
#  },{
#    "region":"B",
#    "name":"banana"
#  }]

# function group_json_by()
#   groups the name of resources by PARAM
#
# usage: echo "{some json}" | group_json_by PARAM
#
# example: 
# > echo "$INPUT" | group_json_by region
#  {
#    "region": "A",
#    "values": [
#      "apple"
#    ]
#  }
#  {
#    "region": "B",
#    "values": [
#      "banana"
#    ]
#  }

group_json_by() {
  jq -r --arg PARAM "$1" 'group_by(.[$PARAM])[]| [{$PARAM:(.[0].[$PARAM]),values:[.[]|.name] }]  | .[] '
}

# function format_for_deletion()
#   creates strings in the correct format to use in gcloud delete commands.
# usage: echo "{some json}" | format_for_deletion PARAM
#
# example: 
# > echo "$INPUT" | format_for_deletion region
#  --region=A apple
#  --region=B banana 

format_for_deletion() {
  group_json_by "$1" |  jq -r --arg PARAM "$1" '"--" + $PARAM + "=" + .[$PARAM] + " " + (.values | join(" "))'
}


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
done

