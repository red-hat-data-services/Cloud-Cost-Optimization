#!/bin/bash 

set -e
# get list of all roles in the account
aws iam list-roles --query 'Roles[*].RoleName'  --output json > roles.json

# each cluster should have a role that matches pattern  
# <cluster-name>-<unique id>-openshift-cluster-csi-drivers-ebs-cloud-credentials ("credentials" could get truncated based on length of cluster name)
cat roles.json | jq -r '.[] | select(test("openshift-cluster-csi-drivers"))' | sed 's/-openshift-cluster.*//' | sed 's/-....$//' |sort |uniq > clusters-with-roles.txt

echo -n > clusters-with-roles-to-delete.txt 

while IFS= read -r CLUSTER_NAME; do
  echo "processing $CLUSTER_NAME..."

  # check to see if cluster name is in OCM
  if ! rosa describe cluster -c $CLUSTER_NAME > /dev/null; then 
    echo "confirmed $CLUSTER_NAME does not exist"; 

    # find the cluster and unique identifier associated with this cluster name and add to list of clusters to delete 
    cat roles.json | jq -r --arg N $CLUSTER_NAME '.[] | select(test($N + "-....-" + "openshift-cluster-csi-drivers"))' | sed 's/-openshift-cluster.*//' \
      >> clusters-with-roles-to-delete.txt
  else
    echo "skipping $CLUSTER_NAME because it appears to exist based on running the command 'rosa describe cluster -c $CLUSTER_NAME' "
  fi
done < clusters-with-roles.txt

# execute operator role deletion
echo "list of roles to delete:"
cat clusters-with-roles-to-delete.txt

NUM_CLUSTERS=(cat clusters-with-roles-to-delete.txt | wc -l)

if [[ "$NUM_CLUSTERS" -lt 20 ]]; then

  echo "running deletion..."
  while IFS= read -r CLUSTER_NAME_PREFIX; do
    rosa delete operator-roles -m auto -y --prefix "$CLUSTER_NAME_PREFIX"
  done < clusters-with-roles-to-delete.txt
else

  echo "More than 20 clusters were marked for deletion, which is anomalous and could indicate an issue with reaching the OCM api. Please verify that the clusters do not in fact exist and run this automation manually with the override enabled"
  exit 1
fi

echo "job complete"
