#!/bin/bash
# Uses the github cli (and jq) to toggle cluster status. Run with no arguments for usage

function usage() {
	echo "Usage: $(basename "$0") resume|hibernate <cluster_name>"
}

if [[ $1 == "resume" ]]; then
	workflow="resume_cluster.yaml"
elif [[ $1 == "hibernate" ]]; then
	workflow="hibernate_cluster.yaml"
else
	usage
	exit 1
fi

if [[ -z "$2" ]]; then
	usage
	exit 1
fi

set -euo pipefail

cluster_name="$2"
ocm_account="PROD (console.redhat.com)"

gh workflow run \
	--repo red-hat-data-services/Cloud-Cost-Optimization \
	-F cluster_name="${cluster_name}" \
	-F ocm_account="${ocm_account}" \
	"${workflow}"

run_id=""
max_retries=120
until [[ -n $run_id ]]; do
	printf "\r %s | 🟡 Waiting for job to start..." "$(date +%H:%M:%S)"
	run_id=$(
		gh run list \
			--workflow=${workflow} \
			--repo red-hat-data-services/Cloud-Cost-Optimization \
			--status="in_progress" \
			--limit 1 \
			--jq '.[0].databaseId' --json databaseId
	)
	max_retries=$((max_retries - 1))
	if [[ ${max_retries} -le 0 ]]; then
		printf "\n🔴 Failed to get job. Check on https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions"
		exit 1
	fi
	sleep 1
done

printf "\n 🟢 Workflow is running"
run_id="$(gh run list \
	--workflow=${workflow} \
	--repo red-hat-data-services/Cloud-Cost-Optimization \
	--limit 1 \
	--jq '.[0].databaseId' --json databaseId)"

printf " run_id=%s\n" "${run_id}"

gh run \
	--repo red-hat-data-services/Cloud-Cost-Optimization \
	watch "${run_id}"
