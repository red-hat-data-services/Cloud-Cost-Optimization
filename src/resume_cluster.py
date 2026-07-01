import json
import sys
import time
from typing import Callable

import boto3
import argparse

import botocore
import requests
import cluster_aggregator as ca

import utils
from utils import InstanceState
from hibernate_cluster import get_all_cluster_details


# === CLUSTER RESUME FUNCTIONS =====================================================================
def resume_generic_cluster(cluster):
    print(f"=== Getting all EC2 instances for region {cluster.region} and cluster {cluster.name} ===",
          flush=True)
    ec2_stopped_map, ec2_running_map = utils.get_stopped_and_running_ec2_maps_for_cluster(cluster)

    print(f"=== Resuming {cluster.name} ===", flush=True)
    if cluster.hcp == "false":
        if cluster.type == 'ocp':
            resume_ipi_cluster(cluster, ec2_stopped_map)
        else:
            if cluster.status == "hibernating":
                resume_cluster(cluster)
            else:
                print(f'Cluster {cluster.name} is not in hibernating state')
    else:
        resume_hypershift_cluster(cluster, ec2_stopped_map, ec2_running_map)


def resume_cluster(cluster):
    utils.run_command(f'script/./resume_cluster.sh {cluster.ocm_account} {cluster.id}')


def resume_hypershift_cluster(cluster:utils.OcCluster, ec2_stopped_map:dict, ec2_running_map=None, wait_for_ready=True, attempts:int=0):
    ec2_client = boto3.client('ec2', region_name=cluster.region)

    # get all stopped instances associated with this cluster
    instances_stopped = [ec2_stopped_map[worker_node] for worker_node in ec2_stopped_map
                         if utils.worker_node_belongs_to_the_hcp_cluster(ec2_stopped_map[worker_node], cluster.name)]
    instances_stopped_ids = [v['InstanceId'] for v in instances_stopped]

    # get all running instances associated with this cluster
    if ec2_running_map is not None:
        instances_running = [ec2_running_map[worker_node] for worker_node in ec2_running_map
                               if utils.worker_node_belongs_to_the_hcp_cluster(ec2_running_map[worker_node], cluster.name)]
    else:
        instances_running = []

    # get the requested node pools for this cluster from OCM
    node_pool_information = get_ocm_node_pool_information(cluster)

    # get the number of instances requested per instance type
    instance_type_counts = {}
    for _, node_pool_info in node_pool_information.items():
        if node_pool_info["instance_type"] not in instance_type_counts:
            instance_type_counts[node_pool_info["instance_type"]] = node_pool_info["replicas"]
        else:
            instance_type_counts[node_pool_info["instance_type"]] += node_pool_info["replicas"]

    # see if we've got the right number of each type of instance
    correct_numbers_of_each_node_type = True
    for instance_type, desired_instance_type_count in instance_type_counts.items():
        _, stopped_count = filter_instances_and_get_count(instances_stopped, instance_type)
        _, running_count = filter_instances_and_get_count(instances_running, instance_type)
        if stopped_count + running_count != desired_instance_type_count:
            correct_numbers_of_each_node_type = False
    total_requested_nodes = sum(v['replicas'] for v in node_pool_information.values())

    if not correct_numbers_of_each_node_type:
        fix_hcp_node_pool_miscount(ec2_client, cluster, node_pool_information, instances_running, instances_stopped)

    elif correct_numbers_of_each_node_type and len(instances_stopped) > 0:
        print(f'\tStarting stopped instances of cluster {cluster.name}: {instances_stopped_ids}', flush=True)

        failed_starts = 0
        for stopped_node in list(instances_stopped_ids):
            try:
                ec2_client.start_instances(InstanceIds=[stopped_node])
            except botocore.exceptions.ClientError as e:
                print(f"\tError starting instance {stopped_node}: {e}", flush=True)
                print(f"\tTerminating unstartable instance {stopped_node}, will fall back to OCM provisioning", flush=True)
                ec2_client.terminate_instances(InstanceIds=[stopped_node])
                instances_stopped_ids.remove(stopped_node)
                instances_stopped = [i for i in instances_stopped if i["InstanceId"] != stopped_node]
                failed_starts += 1

        if failed_starts > 0:
            fix_hcp_node_pool_miscount(ec2_client, cluster, node_pool_information, instances_running, instances_stopped)

    else:
        print(f'All instances for cluster {cluster.name} are already running or initializing.', flush=True)

    if wait_for_ready:
        needs_retry = wait_for_rosa_cluster_to_be_ready(cluster, total_requested_nodes)
        if needs_retry:
            if attempts < 3:
                ec2_stopped_map, ec2_running_map = utils.get_stopped_and_running_ec2_maps_for_cluster(cluster)
                resume_hypershift_cluster(cluster, ec2_running_map, ec2_running_map, attempts+1)
            else:
                raise TimeoutError("Could not fix node pool issues, cluster is not resumable.")

        print(f'Done resuming the cluster {cluster.name}', flush=True)



def get_api_server_base_url(ocm_account: str):
    return 'https://api.openshift.com/api' if ocm_account == 'PROD' else 'https://api.stage.openshift.com/api'


def get_ocm_node_pool_information(cluster:utils.OcCluster) -> dict:
    """
    Get the requested node pool size and instance kind for each node pool in a cluster

    Returns a dict of node pool ID - > {"replicas": requested replica count, "instance_type": AWS instance type}

    e.g.,:
    {"workers": {"replicas": 2, "instance_type": "m5.2xlarge"}
    """
    api_server_base_url = get_api_server_base_url(cluster.ocm_account)
    ocm_api_token = utils.get_ocm_api_token()
    node_pools_response = requests.get(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools',
                                       headers={'Authorization': f'Bearer {ocm_api_token}'})
    node_pools = node_pools_response.json()
    node_pool_dict = {}

    for node_pool in node_pools["items"]:
        if node_pool['kind'] == 'NodePool':
            if "replicas" in node_pool:
                replicas = node_pool['replicas']
            elif "autoscaling" in node_pool and "min_replica" in node_pool["autoscaling"]:
                replicas = node_pool['autoscaling']['min_replica']
            else:
                raise KeyError(f"Retrieved node pool information did not report a desired replica count, instead received: {node_pool}")

            if "aws_node_pool" in node_pool and "instance_type" in node_pool["aws_node_pool"]:
                instance_type = node_pool["aws_node_pool"]["instance_type"]
            else:
                raise KeyError(f"Retrieved node pool information did not report a replica count, instead received: {node_pool}")

            if "status" in node_pool and "current_replicas" in node_pool["status"]:
                current_replicas = node_pool["status"]["current_replicas"]
            else:
                raise KeyError(f"Retrieved node pool information did not report a current replica count, instead received: {node_pool}")

            node_pool_dict[node_pool['id']] = {"replicas": replicas, "instance_type": instance_type, "current_replicas": current_replicas}

    return node_pool_dict


def set_node_pool_to_n_replicas(url: str, token: str, cluster_id: str, node_pool_id, n_replicas: int):
    """
    Update a node to pool to the requested replica count
    """
    payload = {'id': node_pool_id, 'labels': {}, 'taints': [], 'replicas': n_replicas}
    response = requests.patch(f'{url}/clusters_mgmt/v1/clusters/{cluster_id}/node_pools/{node_pool_id}',
                              data=json.dumps(payload),
                              headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    if response.status_code != 200:
        raise RuntimeError(f"Could not set node pool {node_pool_id} replicas to {n_replicas}, error {response.status_code}: {response.text}")


def filter_instances_and_get_count(instance_list, instance_type):
    """
    Helper function to return:
        - the sublist of instances that match a specified instance type
        - the count of such instances
    """
    matching_ids = []
    for n in instance_list:
        if n["InstanceType"] == instance_type:
            matching_ids.append(n["InstanceId"])
    return matching_ids, len(matching_ids)


def trigger_ocm_reprovision_of_missing_nodes(cluster, node_pool_setter: Callable, node_pool_id: str, instance_type:str, nodes_present: int, nodes_desired: int):
    nodes_missing = nodes_desired - nodes_present

    print(f"\tTemporarily increasing node pool {node_pool_id} to {nodes_desired + nodes_missing}",
              flush=True)
    node_pool_setter(node_pool_id, nodes_desired + nodes_missing)

    node_pool_info = get_ocm_node_pool_information(cluster)
    while not all(node_pool["current_replicas"]>=node_pool["replicas"] for node_pool_id, node_pool in node_pool_info.items()):
        print(f"\tWaiting for new nodes to provision and register, currently have:", flush=True)
        for node_pool_id, node_pool in node_pool_info.items():
            print(f"\t\t- {node_pool_id}: {node_pool['current_replicas']}/{node_pool['replicas']}", flush=True)

        node_pool_info = get_ocm_node_pool_information(cluster)
        time.sleep(5)

    # reset the machine pool back to the original node quantity
    print(f"\tResetting node pool {node_pool_id} to {nodes_desired}", flush=True)
    node_pool_setter(node_pool_id, nodes_desired)


def robust_node_start(ec2_client, cluster, nodes_to_start: list[str], node_pool_setter: Callable, node_pool_id: str, instance_type: str, nodes_desired: int):
    """ Try directly starting a list of node IDS, fallback to OCM provisioning if starting fails"""

    successful_starts, failed_starts = 0, 0
    for stopped_node in nodes_to_start:
        try:
            ec2_client.start_instances(InstanceIds=[stopped_node])
            successful_starts += 1
        except botocore.exceptions.ClientError as e:
            print(f"\tError starting instance {stopped_node}: {e}", flush=True)
            print(f"\tTerminating unstartable instance {stopped_node}, will fall back to OCM provisioning", flush=True)
            ec2_client.terminate_instances(InstanceIds=[stopped_node])
            failed_starts += 1

    if failed_starts > 0:
        trigger_ocm_reprovision_of_missing_nodes(
            cluster=cluster,
            node_pool_setter=node_pool_setter,
            node_pool_id=node_pool_id,
            instance_type=instance_type,
            nodes_present=nodes_desired - failed_starts,
            nodes_desired=nodes_desired
        )



def fix_hcp_node_pool_miscount(ec2_client, cluster:utils.OcCluster, node_pool_information: dict, running_instances: list, stopped_instances: list):
    """
    Fix a mismatch between the number of existing instances (running or stopped) and the desired
    node pool size
    """
    api_server_base_url = get_api_server_base_url(cluster.ocm_account)
    ocm_api_token = utils.get_ocm_api_token()
    node_pool_setter = lambda np_id, n: set_node_pool_to_n_replicas(
        url=api_server_base_url,
        token=ocm_api_token,
        cluster_id=cluster.id,
        node_pool_id=np_id,
        n_replicas=n)

    for node_pool_id, node_pool_info in node_pool_information.items():
        n_requested_nodes = node_pool_info['replicas']

        matching_running_ids, n_matching_running_nodes = filter_instances_and_get_count(running_instances, node_pool_info['instance_type'])
        matching_stopped_ids, n_matching_stopped_nodes = filter_instances_and_get_count(stopped_instances, node_pool_info['instance_type'])
        n_actual_nodes = n_matching_running_nodes + n_matching_stopped_nodes

        if n_actual_nodes<n_requested_nodes:
            # If number of actual nodes is too small:
            #   1) start all stopped nodes
            #   2) prune the machine pool down to the existing node count
            #   3) reset it back to the desired amount,
            # The prune+reset should trigger the creation of the missing nodes
            print(f"=== Rectifying node undersupply for node pool {node_pool_id} ({node_pool_info['instance_type']}) (have {n_actual_nodes} nodes, want {n_requested_nodes}) ===", flush=True)

            if n_matching_stopped_nodes > 0:
                print(f"\tStarting stopped nodes of node pool {node_pool_id}: {matching_stopped_ids }", flush=True)
                for stopped_node in matching_stopped_ids:
                    try:
                        ec2_client.start_instances(InstanceIds=[stopped_node])
                    except botocore.exceptions.ClientError as e:
                        print(f"\tError starting instance {stopped_node}: {e}", flush=True)
                        print(f"\tTerminating unstartable instance {stopped_node}, will fall back to OCM provisioning",
                              flush=True)
                        ec2_client.terminate_instances(InstanceIds=[stopped_node])
                        n_actual_nodes -= 1

            trigger_ocm_reprovision_of_missing_nodes(
                cluster=cluster,
                node_pool_setter=node_pool_setter,
                node_pool_id=node_pool_id,
                instance_type=node_pool_info["instance_type"],
                nodes_present=n_actual_nodes,
                nodes_desired=n_requested_nodes
            )

        elif n_actual_nodes > n_requested_nodes:
            # if number of actual nodes is too big, where D=desired node count, R=running node count, S=stopped_node count
            #   1) If we already have sufficient running nodes for the requested node count (R>D):
            #     a) terminate all stopped nodes S
            #     b) terminate any extra running nodes R-D
            #      Outcome: R-(R-D) + (S-S) = D nodes
            #   2) Else:
            #     a) leave all running nodes R running
            #     b) terminate (R+S)-D stopped nodes
            #     c) start the remaining S-((R+S)-D) nodes
            #       Outcome: R + (S - ((R+S)-D)) = D nodes

            print(f"=== Rectifying node oversupply for node pool {node_pool_id} (have {n_actual_nodes} nodes, want {n_requested_nodes}) ===", flush=True)

            # if we've got more running nodes than needed, terminate all stopped nodes and the extra running
            if n_matching_running_nodes>=n_requested_nodes:
                if n_matching_running_nodes>n_requested_nodes:
                    running_nodes_to_terminate = matching_running_ids[n_requested_nodes:]
                    print(f"\tTerminating extra running instances: {running_nodes_to_terminate}", flush=True)
                    ec2_client.terminate_instances(InstanceIds=running_nodes_to_terminate)

                if n_matching_stopped_nodes>0:
                    print(f"\tTerminating all stopped instances: {matching_stopped_ids}", flush=True)
                    ec2_client.terminate_instances(InstanceIds=matching_stopped_ids)
            else:
                # some combination of running and stopped nodes overshoots the desired amount
                overshoot = n_actual_nodes - n_requested_nodes

                # since we've checked that there are fewer running nodes than desired
                # we can always fix the overshoot by terminating stopped nodes
                stopped_nodes_to_terminate = matching_stopped_ids[:overshoot]
                print(f"\tTerminating extra stopped instances: {stopped_nodes_to_terminate}", flush=True)
                ec2_client.terminate_instances(InstanceIds=stopped_nodes_to_terminate)

                # start remaining instances
                stopped_nodes_to_start = matching_stopped_ids[overshoot:]
                print(f"\tStarting remaining stopped instances: {stopped_nodes_to_start}", flush=True)
                robust_node_start(
                    ec2_client=ec2_client,
                    cluster=cluster,
                    nodes_to_start=stopped_nodes_to_start,
                    instance_type=node_pool_info["instance_type"],
                    node_pool_setter=node_pool_setter,
                    node_pool_id=node_pool_id,
                    nodes_desired=n_requested_nodes
                )

        else:
            #  node pool has the correct number of nodes
            pass


def wait_for_rosa_cluster_to_be_ready(cluster:utils.OcCluster, worker_count:int, timeout_seconds:int=1200) -> bool:
    deadline = time.time() + timeout_seconds

    time.sleep(15)
    ec2_map = utils.get_instances(cluster=cluster)
    states = utils.group_ec2_instances_by_state(ec2_map)

    print(f"=== Waiting for {worker_count} worker nodes to start ===", flush=True)
    while len(states[InstanceState.running]) < worker_count:
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for {worker_count} nodes to start (only {len(states[InstanceState.running])} running after {timeout_seconds}s)")
        print(f"\t{len(states[InstanceState.running])}/{worker_count} nodes running:", flush=True)
        utils.print_ec2_instance_state(ec2_map, prefix="\t\t", filtered_states=[InstanceState.terminated])

        time.sleep(5)

        ec2_map = utils.get_instances(cluster=cluster)
        states = utils.group_ec2_instances_by_state(ec2_map)

        # check if there's enough starting nodes - no point waiting if not.
        starting_nodes = len(states[InstanceState.running]) + len(states[InstanceState.pending])
        if starting_nodes < worker_count:
            print(f"\tWARNING: There are only {starting_nodes} nodes in a pending or running state, "
                  f"the cluster likely needs further intervention. The script will wait one minute to see if more"
                  f" pending nodes arrive, and if not will attempt to repair the cluster nodes", flush=True)
            insufficent_node_timeout = time.time()
        else:
            insufficent_node_timeout = None

        # terminate the wait loop if it's hopeless
        if insufficent_node_timeout is not None and time.time() > (insufficent_node_timeout + 60):
            print("\tNo new pending nodes arrived- waiting longer is probably not going to be helpful. Beginning node pool repair.", flush=True)
            return True # need to retry resumption



    print("\tAll nodes running", flush=True)
    status_map = get_instance_and_system_status(cluster, states[InstanceState.running])
    while set(status_map.values()) != set(['ok_ok']):
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for nodes to report ok status after {timeout_seconds}s. Current: {status_map}")
        print("\tWaiting for all worker nodes to report status=ok_ok:", flush=True)
        for k,v in status_map.items():
            print(f"\t\t- {k}: {v}", flush=True)
        time.sleep(5)
        status_map = get_instance_and_system_status(cluster, states[InstanceState.running])

    return False

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


def wait_for_ipi_cluster_to_be_ready(cluster:utils.OcCluster, worker_count:int, timeout_seconds:int=1200):
    deadline = time.time() + timeout_seconds
    time.sleep(15)
    ec2_map = utils.get_instances(cluster=cluster, current_state=InstanceState.running)
    InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map
                   if ec2_name.startswith(f'{cluster.internal_name}-')
                   and utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[ec2_name], cluster.internal_name)]
    while len(InstanceIds) < worker_count:
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for {worker_count} nodes to start (only {len(InstanceIds)} running after {timeout_seconds}s)")
        print(f'\t{len(InstanceIds)}/{worker_count} nodes running, will check again in 5s...', flush=True)
        time.sleep(5)
        ec2_map = utils.get_instances(cluster=cluster, current_state=InstanceState.running)
        InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map
                       if ec2_name.startswith(f'{cluster.internal_name}-')
                       and utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[ec2_name], cluster.internal_name)]
    print("All nodes running", flush=True)

    status_map = get_instance_and_system_status(cluster, InstanceIds)
    while set(status_map.values()) != set(['ok_ok']):
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for nodes to report ok status after {timeout_seconds}s. Current: {status_map}")
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
    clusters = get_all_cluster_details(args.ocm_account)

    available_clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']
    target_cluster = [cluster for cluster in available_clusters if cluster.name == utils.sanitize_cluster_name(args.cluster_name)]
    if len(target_cluster) > 1:
        sys.exit("More than one clusters found with given name.")

    if not target_cluster:
        sys.exit("No cluster found with given name.")

    if len(target_cluster) == 1:
        target_cluster = target_cluster[0]
        resume_generic_cluster(target_cluster)

        print("=== Updating cluster status in Smartsheet ===")
        ca.main(cluster_list=[target_cluster], needs_data_refresh=False, allow_smartsheet_deletion=False)

        print(f'Resumed cluster: {target_cluster.name}', flush=True)


if __name__ == '__main__':
    main()
