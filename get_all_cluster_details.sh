#!/bin/bash

ocm_account=$1

if [[ $ocm_account == "PROD" ]]
then
  ocm login --token="${OCM_TOKEN}"
elif [[ $ocm_account == "STAGE" ]]
then
  ocm login --token="${OCM_TOKEN}" --url stage
else
  echo "ERROR: unsupported OCM Account ${ocm_account}"
  exit 1
fi


ocm list clusters
ocm list clusters --no-headers > clusters_${ocm_account}.txt
