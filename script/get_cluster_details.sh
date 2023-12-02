#!/bin/bash

ocm_account=$1
cluster_id=$2
ocm logout

if [[ $ocm_account == "PROD" ]]
then
  ocm login --client-id=${OCM_CLIENT_ID} --client-secret=${OCM_CLIENT_SECRET}
elif [[ $ocm_account == "STAGE" ]]
then
  ocm login --client-id=${OCM_CLIENT_ID} --client-secret=${OCM_CLIENT_SECRET} --url stage
else
  echo "ERROR: unsupported OCM Account ${ocm_account}"
  exit 1
fi

creation_date=$(ocm get /api/accounts_mgmt/v1/subscriptions -p search="cluster_id='${cluster_id}'" | jq -r ".items|.[]|.created_at")
creator_id=$(ocm get /api/accounts_mgmt/v1/subscriptions -p search="cluster_id='${cluster_id}'" | jq -r ".items|.[]|.creator.id")
creator_name=$(ocm get /api/accounts_mgmt/v1/accounts/${creator_id} | jq -r '.first_name + " " + .last_name')
creator_email=$(ocm get /api/accounts_mgmt/v1/accounts/${creator_id} | jq -r '.email')

cp refs/cluster_details.json ${cluster_id}_details.json
sed -i "s,cluster_id,${cluster_id},g" ${cluster_id}_details.json
sed -i "s,cluster_creation_date,${creation_date},g" ${cluster_id}_details.json
sed -i "s,cluster_creator_name,${creator_name},g" ${cluster_id}_details.json
sed -i "s,cluster_creator_email,${creator_email},g" ${cluster_id}_details.json

cat ${cluster_id}_details.json





