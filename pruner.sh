
# gcloud config set project $1

# get google dns managed zones

# usage: echo "{some json}" | group_json_by PARAM
group_json_by() {
jq -r --arg PARAM "$1" 'group_by(.[$PARAM])[]| [{$PARAM:(.[0].[$PARAM]),values:[.[]|.name] }]  | .[] '
}

# usage: echo "{some json}" | format_for_deletion PARAM
format_for_deletion() {
group_json_by $1 |  jq -r --arg PARAM "$1" '"--" + $PARAM + "=" + .[$PARAM] + " " + (.values | join(" "))'
}

ZONES=$(gcloud dns managed-zones list --filter="name ~ ^ci-op AND creationTime < $TWO_DAYS_AGO" --format json | jq -r '.[].name')

# use the list of zones to get the total list of ci jobs.
OLD_JOBS=$(echo "$ZONES" | sed 's/-private-zone$//')

for OLD_JOB in $OLD_JOBS; do
  echo $OLD_JOB
  SVC_ACCTS=$(gcloud iam service-accounts list --filter "displayName ~ $OLD_JOB" --format json | jq -r '.[].displayName')
  for SVC_ACCT in $SVC_ACCTS; do
    break
    # TODO delete service accounts 
  done
 
  # can just delete instance groups instead of instances, I think 
  # gcloud compute instances list --filter "name ~ $OLD_JOB" --format json | jq -r '.[].name'
  gcloud compute instance-groups list --filter "name ~ $OLD_JOB" --format json |  format_for_deletion zone

  FW_RULES=$(gcloud compute firewall-rules list --format json --filter "network ~ $OLD_JOB" | jq -r '.[].name')
  echo $FW_RULES | xargs gcloud compute firewall-rules delete

  ROUTERS=$(gcloud compute routers list --filter "name ~ $OLD_JOB" --format json) 
  echo $ROUTERS |  format_for_deletion region | xargs -L 1 gcloud compute routers delete --quiet

  FWD_RULES=$(gcloud compute forwarding-rules list --filter "name ~ $OLD_JOB" --format json)
  echo $FWD_RULES | format_for_deletion region | xargs -L 1 gcloud compute forwarding-rules delete --quiet

  ADDRS=$(gcloud compute addresses list --filter "name ~ $OLD_JOB" --format json) 
  echo $ADDRS | format_for_deletion region | xargs -L 1 gcloud compute addresses delete --quiet

  SUBNETS=$(gcloud compute networks subnets list --filter "name ~ $OLD_JOB" --format json)
  echo $SUBNETS | format_for_deletion region | xargs -L 1 gcloud compute networks subnets delete --quiet


  NETWORKS=$(gcloud compute networks list --filter "name ~ $OLD_JOB" --format json | jq -r '.[].name')
  echo $NETWORKS | xargs gcloud compute networks delete --quiet
  break
done





# Delete zones last because we are leveraging it early to get the entire list of jobs. 
# If we deleted them first and there's an error later, we wouldn't be able to get back the whole list of jobs
for ZONE in $ZONES; do
  # TODO delete managed zones
  # dns records are attached to zones, so deleting the zones should take care of the records too
  break
done
