import sys
import time
import boto3
import argparse
import cluster_aggregator as ca

import utils
from utils import InstanceState


def get_all_cluster_details(ocm_account:str):
    utils.get_cluster_list(ocm_account)
    clusters = []
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        cluster = utils.OcCluster(cluster_detail, ocm_account)
        if cluster.type == 'ocp':
            utils.get_ipi_cluster_name(cluster)
        if cluster.cloud_provider == 'aws' and (
                cluster.type != 'ocp' or (cluster.type == 'ocp' and cluster.name != cluster.internal_name)):
            clusters.append(cluster)
    return clusters


# === CLUSTER HIBERNATION FUNCTIONS ==== ===========================================================
def hibernate_generic_cluster(cluster):
    print(f"=== Getting all EC2 instances for region {cluster.region} and cluster {cluster.name} ===",
          flush=True)
    ec2_map = utils.get_instances(
        cluster=cluster,
        current_state=[InstanceState.running, InstanceState.pending],
    )

    print(f"=== Hibernating {cluster.name} ===", flush=True)
    if cluster.hcp == "false":
        if cluster.type == 'ocp':
            hibernate_ipi_cluster(cluster, ec2_map)
        else:
            if cluster.status == "ready":
                hibernate_cluster(cluster)
            else:
                print(
                    f'Cluster {cluster.name} is not in ready state, please wait for it to be ready and try again',
                    flush=True
                )
    else:
        hibernate_hypershift_cluster(cluster, ec2_map)


def hibernate_cluster(cluster):
    utils.run_command(f'script/./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')


def hibernate_ipi_cluster(cluster, ec2_map:dict):
    result = False

    # The startswith pre-filter may miss IPI nodes — it caused a bug in HCP clusters.
    # If IPI nodes aren't being correctly hibernated, investigate this filter first.
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.internal_name}-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [
        ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes
        if utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[worker_node], cluster.internal_name)]
    if len(InstanceIds) > 0:
        print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
        print(f'Started hibernating the cluster {cluster.name}')
        result = True
    else:
        print(f'Cluster {cluster.name} is already hibernated.')
    return result


def hibernate_hypershift_cluster(cluster:utils.OcCluster, ec2_map:dict, wait_for_stop=True, cleanup_volumes=True):
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in ec2_map if
                   utils.worker_node_belongs_to_the_hcp_cluster(ec2_map[worker_node], cluster.name)]

    if len(InstanceIds) > 0:
        print(f'Stopping running worker instances of cluster {cluster.name}: {InstanceIds}', flush=True)
        worker_count = len(InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
        if wait_for_stop:
            wait_for_rosa_cluster_to_be_hibernated(cluster, worker_count)
        if cleanup_volumes:
            root_devices = {inst['InstanceId']: inst['RootDeviceName'] for inst in ec2_map.values() if inst['InstanceId'] in InstanceIds}
            filters = [{'Name': 'attachment.instance-id', 'Values': InstanceIds}]
            attached_volumes = ec2_client.describe_volumes(Filters=filters)
            attached_volumes = [attachment for volume in attached_volumes['Volumes'] for attachment in volume['Attachments']
                                if attachment['DeleteOnTermination'] == True
                                and attachment['Device'] != root_devices.get(attachment['InstanceId'])
                                and not utils.check_if_given_tag_exists('KubernetesCluster', volume.get('Tags', []))]
            print('attached_volumes', attached_volumes, flush=True)
            for volume in attached_volumes:
                print(f'detaching the volume {volume["VolumeId"]}', flush=True)
                ec2_client.detach_volume(Device=volume['Device'], InstanceId=volume['InstanceId'], VolumeId=volume['VolumeId'])
            for volume in attached_volumes:
                print(f'deleting the volume {volume["VolumeId"]}', flush=True)
                utils.delete_volume(volume['VolumeId'], cluster.region)
        print(f'Done hibernating the cluster {cluster.name}', flush=True)
    else:
        print(f'Cluster {cluster.name} is already hibernated.', flush=True)


def wait_for_rosa_cluster_to_be_hibernated(cluster:utils.OcCluster, worker_count:int):
    time.sleep(15)
    ec2_map = utils.get_instances(cluster=cluster)
    states = utils.group_ec2_instances_by_state(ec2_map)

    print(f"Waiting for {worker_count} worker nodes to stop, please wait...", flush=True)
    while len(states[InstanceState.stopped]) < worker_count:
        print(f"\t{len(states[InstanceState.stopped])}/{worker_count} nodes are stopped.", flush=True)
        utils.print_ec2_instance_state(ec2_map, prefix="\t\t", filtered_states=[InstanceState.terminated])

        time.sleep(5)
        ec2_map = utils.get_instances(cluster=cluster)
        states = utils.group_ec2_instances_by_state(ec2_map)

    print("All nodes stopped", flush=True)



    state_map = utils.get_instance_state_by_id(cluster, states[InstanceState.stopped])
    while set(state_map.values()) != set([InstanceState.stopped]):
        print('\tWaiting for worker nodes to report status=stopped, will check again in 5s...', flush=True)
        time.sleep(5)
        state_map = utils.get_instance_state_by_id(cluster, states[InstanceState.stopped])


def wait_for_ipi_cluster_to_be_hibernated(cluster:utils.OcCluster, worker_count:int):
    time.sleep(15)
    ec2_map = utils.get_instances(cluster=cluster, current_state=InstanceState.stopped)
    InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map
                   if ec2_name.startswith(f'{cluster.internal_name}-')
                   and utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[ec2_name], cluster.internal_name)]

    print(f"=== Waiting for {len(InstanceIds)} worker nodes to stop ===", flush=True)
    while len(InstanceIds) < worker_count:
        print(f'\t{len(InstanceIds)}/{worker_count} nodes are stopped:', flush=True)
        time.sleep(5)
        ec2_map = utils.get_instances(cluster=cluster, current_state=InstanceState.stopped)
        InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map
                       if ec2_name.startswith(f'{cluster.internal_name}-')
                       and utils.worker_node_belongs_to_the_ipi_cluster(ec2_map[ec2_name], cluster.internal_name)]

    print("\tAll nodes stopped", flush=True)

    state_map = utils.get_instance_state_by_id(cluster, InstanceIds)
    while set(state_map.values()) != set([InstanceState.stopped]):
        print('\tWaiting for worker nodes to report status=stopped, will check again in 5s...', flush=True)
        time.sleep(5)
        state_map = utils.get_instance_state_by_id(cluster, InstanceIds)


# === MAIN FUNCTION ================================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Hibernate the given cluster"
    )

    parser.add_argument("--cluster_name", dest="cluster_name",
                        action="store",
                        help="Provide the cluster name to hibernate", required=True)

    parser.add_argument("--ocm_account", dest="ocm_account",
                        action="store",
                        help="Provide the OCM account which cluster belongs to , possible values PROD or STAGE", required=True)
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
        sys.exit("More than one clusters found with give name.")

    if not target_cluster:
        sys.exit("No cluster found with given name.")

    if len(target_cluster) == 1:
        target_cluster = target_cluster[0]
        hibernate_generic_cluster(target_cluster)

        print("=== Updating cluster status in Smartsheet ===")
        ca.main(cluster_list=[target_cluster], needs_data_refresh=False, allow_smartsheet_deletion=False)

        print(f'Hibernated cluster: {target_cluster.name}')


if __name__ == '__main__':
    main()
