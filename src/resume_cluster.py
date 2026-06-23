import json
import sys
import time
import boto3
import argparse
import requests
import cluster_aggregator as ca

import utils
from hibernate_cluster import get_all_cluster_details


# === CLUSTER RESUME FUNCTIONS =====================================================================
def resume_cluster(cluster):
    utils.run_command(f'script/./resume_cluster.sh {cluster.ocm_account} {cluster.id}')


def resume_hypershift_cluster(cluster:utils.OcCluster, ec2_map:dict, ec2_running_map=None, wait_for_ready=True):
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    instances_stopped = [ec2_map[worker_node] for worker_node in ec2_map
                   if utils.worker_node_belongs_to_the_hcp_cluster(ec2_map[worker_node], cluster.name)]
    instances_stopped_ids = [v['InstanceId'] for v in instances_stopped]

    if ec2_running_map is not None:
        instances_running = [ec2_running_map[worker_node] for worker_node in ec2_running_map
                               if utils.worker_node_belongs_to_the_hcp_cluster(ec2_running_map[worker_node], cluster.name)]
    else:
        instances_running = []

    print("== RUNNING INSTANCES ==")
    print(instances_running,flush=True)
    print("== STOPPED INSTANCES ==")
    print(instances_stopped, flush=True)
    return


    node_pool_info = get_ocm_node_pool_information(cluster)
    total_requested_nodes = sum(v['replicas'] for v in node_pool_info.values())
    actual_nodes = len(instances_stopped) + len(instances_running)

    if actual_nodes != total_requested_nodes:
        print(f"Correcting mismatch in node count. "
              f"Requested nodes: {total_requested_nodes}. Actual nodes: {actual_nodes}, "
              f"({len(instances_running)} running and {len(instances_stopped)} stopped nodes).")
        worker_count = sync_hcp_node_pools(cluster, node_pool_info, instances_running, instances_stopped)
    elif actual_nodes == total_requested_nodes and len(instances_stopped) > 0:
        print(f'Starting worker instances of cluster {cluster.name}: {instances_stopped_ids}', flush=True)
        worker_count = len(instances_stopped)
        ec2_client.start_instances(InstanceIds=instances_stopped_ids)
    else:
        print(f'Cluster {cluster.name} is already running.', flush=True)
        return

    if wait_for_ready:
        wait_for_rosa_cluster_to_be_ready(cluster, worker_count)
        print(f'Done resuming the cluster {cluster.name}', flush=True)


def get_ocm_node_pool_information(cluster:utils.OcCluster) -> dict:
    """
    Get the requested node pool size and instance kind for each node pool in a cluster

    Returns a dict of node pool ID - > {"replicas": requested replica count, "instance_type": AWS instance type}

    e.g.,:
    {"workers": {"replicas": 2, "instance_type": "m5.2xlarge"}
    """
    api_server_base_url = 'https://api.openshift.com/api' if cluster.ocm_account == 'PROD' else 'https://api.stage.openshift.com/api'
    ocm_api_token = utils.get_ocm_api_token()
    node_pools_response = requests.get(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools',
                                       headers={'Authorization': f'Bearer {ocm_api_token}'})
    node_pools = node_pools_response.json()
    node_pools = {node_pool['id']: {"replicas": node_pool['replicas'], "instance_type": node_pool["aws_node_pool"]["instance_type"]}
                  for node_pool in node_pools['items'] if node_pool['kind'] == 'NodePool'}
    return node_pools


def sync_hcp_node_pools(cluster:utils.OcCluster, node_pool_information: dict, instances_running: list, instances_stopped: list):
    print("=== Syncing node pools ===", flush=True)
    for id, node_pool_data in node_pool_information.items():



        newReplicas = replicas+1 if replicas <= 2 else replicas-1
        payload = {'id': id, 'labels': {}, 'taints': [], 'replicas': newReplicas}
        response = requests.patch(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools/{id}',
                                  data=json.dumps(payload),
                                  headers={'Authorization': f'Bearer {ocm_api_token}', 'Content-Type': 'application/json'})

        print(f'synced the machine pool {id} with the new replica count {newReplicas} for cluster {cluster.name}', flush=True)
        if response.status_code == 200:
            totalNodes += newReplicas
            print(f'now total nodes are {totalNodes}', flush=True)

        payload = {'id': id, 'labels': {}, 'taints': [], 'replicas': replicas}
        response = requests.patch(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools/{id}',
                                  data=json.dumps(payload),
                                  headers={'Authorization': f'Bearer {ocm_api_token}', 'Content-Type': 'application/json'})

        print(f'reset the machine pool {id} with the original replica count {replicas} for cluster {cluster.name}', flush=True)
        if response.status_code == 200:
            totalNodes += replicas - newReplicas
            print(f'now total nodes are back to {totalNodes}', flush=True)

    time.sleep(30)
    return totalNodes


def wait_for_rosa_cluster_to_be_ready(cluster:utils.OcCluster, worker_count:int):
    time.sleep(15)
    ec2_map = utils.get_instances_for_region_and_cluster_name(cluster.region, 'running', cluster.name)
    InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map]
    print(f"Waiting for {len(InstanceIds)} worker nodes to start, please wait...", flush=True)
    while len(InstanceIds) < worker_count:
        print(f'\t{len(InstanceIds)}/{worker_count} nodes running, will check again in 5s...', flush=True)
        time.sleep(5)
        ec2_map = utils.get_instances_for_region_and_cluster_name(cluster.region, 'running', cluster.name)
        InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map]
    print("All nodes running", flush=True)

    status_map = get_instance_and_system_status(cluster, InstanceIds)
    while set(status_map.values()) != set(['ok_ok']):
        print('\tWaiting for worker nodes to report status=ok, will check again in 5s...', flush=True)
        time.sleep(5)
        status_map = get_instance_and_system_status(cluster, InstanceIds)


def resume_ipi_cluster(cluster:utils.OcCluster, ec2_map:dict, wait_for_ready=True):

    # The startswith pre-filter may miss IPI nodes — it caused a bug in HCP clusters.
    # If IPI nodes aren't being correctly resumed, investigate this filter first.
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.internal_name}-')]

    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes
                   if utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[worker_node], cluster.internal_name)]

    if len(InstanceIds) > 0:
        print(f'Starting Worker Instances of cluster {cluster.name}', InstanceIds, flush=True)
        worker_count = len(InstanceIds)
        ec2_client.start_instances(InstanceIds=InstanceIds)
        if wait_for_ready:
            wait_for_ipi_cluster_to_be_ready(cluster, worker_count)
        print(f'Done resuming the cluster {cluster.name}', flush=True)
    else:
        print(f'Cluster {cluster.name} is already running.', flush=True)


def wait_for_ipi_cluster_to_be_ready(cluster:utils.OcCluster, worker_count:int):
    time.sleep(15)
    ec2_map = utils.get_instances_for_region(cluster.region, 'running')
    InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map
                   if ec2_name.startswith(f'{cluster.internal_name}-')
                   and utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[ec2_name], cluster.internal_name)]
    while len(InstanceIds) < worker_count:
        print(f'\t{len(InstanceIds)}/{worker_count} nodes running, will check again in 5s...', flush=True)
        time.sleep(5)
        ec2_map = utils.get_instances_for_region(cluster.region, 'running')
        InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map
                       if ec2_name.startswith(f'{cluster.internal_name}-')
                       and utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[ec2_name], cluster.internal_name)]
    print("All nodes running", flush=True)

    status_map = get_instance_and_system_status(cluster, InstanceIds)
    while set(status_map.values()) != set(['ok_ok']):
        print('Worker nodes initializing, please wait...', flush=True)
        time.sleep(5)
        status_map = get_instance_and_system_status(cluster, InstanceIds)


def get_instance_and_system_status(cluster:utils.OcCluster, InstanceIds:list):
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    ec2_map = ec2_client.describe_instance_status(InstanceIds=InstanceIds)
    status_map = {ec2['InstanceId']:f"{ec2['InstanceStatus']['Status']}_{ec2['SystemStatus']['Status']}" for ec2 in ec2_map['InstanceStatuses']}
    return status_map


# === MAIN FUNCTION ================================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Resume the given cluster"
    )

    parser.add_argument("--cluster_name", dest="cluster_name",
                        action="store",
                        help="Provide the cluster name to resume", required=True)

    parser.add_argument("--ocm_account", dest="ocm_account",
                        action="store",
                        help="Provide the OCM account which cluster belongs to, possible values PROD or STAGE", required=True)
    args = parser.parse_args()

    return args


def main():
    args = parse_arguments()
    args.ocm_account = args.ocm_account.split(' ')[0]

    print("=== Getting details for all clusters ===", flush=True)
    clusters = []
    get_all_cluster_details(args.ocm_account, clusters)

    available_clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']
    target_cluster = [cluster for cluster in available_clusters if cluster.name == utils.sanitize_cluster_name(args.cluster_name)]
    if len(target_cluster) > 1:
        sys.exit("More than one clusters found with given name.")

    if not target_cluster:
        sys.exit("No cluster found with given name.")

    if len(target_cluster) == 1:
        target_cluster = target_cluster[0]

        print(f"=== Getting all EC2 instances for region {target_cluster.region} and cluster {target_cluster.name} ===", flush=True)
        ec2_map = utils.get_instances_for_region_and_cluster_name(target_cluster.region, 'stopped', target_cluster.name)
        ec2_running_map = utils.get_instances_for_region_and_cluster_name(target_cluster.region, 'running', target_cluster.name)

        print(f"=== Resuming {target_cluster.name} ===", flush=True)
        if target_cluster.hcp == "false":
            if target_cluster.type == 'ocp':
                resume_ipi_cluster(target_cluster, ec2_map)
            else:
                if target_cluster.status == "hibernating":
                    resume_cluster(target_cluster)
                else:
                    print(f'Cluster {target_cluster.name} is not in hibernating state')
        else:
            resume_hypershift_cluster(target_cluster, ec2_map, ec2_running_map)

        print("=== Updating cluster status in Smartsheet ===")
        ca.main(cluster_list=[target_cluster], needs_data_refresh=False, allow_smartsheet_deletion=False)

        print(f'Resumed cluster: {target_cluster.name}', flush=True)


if __name__ == '__main__':
    main()
