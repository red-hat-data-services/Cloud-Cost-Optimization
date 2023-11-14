import json
import sys

import boto3
import os
import argparse
import cluster_aggregator as ca


class oc_cluster:
    def __init__(self, cluster_detail, ocm_account):
        details = cluster_detail.split(' ')
        details = [detail for detail in details if detail]
        self.id = details[0]
        self.name = details[1]
        self.api_url = details[2]
        self.ocp_version = details[3]
        self.type = details[4]
        self.hcp = details[5]
        self.cloud_provider = details[6]
        self.region = details[7]
        self.status = details[8]
        self.nodes = []
        self.hibernate_error = ''
        self.ocm_account = ocm_account
def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_instances_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': [current_state]}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=1000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']: instance for instance in
               ec2_map}
    print(region, len(ec2_map))
    return ec2_map

def get_all_instances(ec2_instances, current_state):
    client = boto3.client('ec2')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        ec2_instances[region] = get_instances_for_region(region, current_state)


def get_cluster_list(ocm_account:str):
    run_command(f'./get_all_cluster_details.sh {ocm_account}')

def hybernate_hypershift_cluster(cluster:oc_cluster, ec2_map:dict):
    # ec2_map = ec2_instances[cluster.region]
    print([name for name in ec2_map])
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-workers-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes]
    if len(InstanceIds) > 0:
        print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
    else:
        print(f'Cluster {cluster.name} is already hibernated.')



def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def hibernate_cluster(cluster: oc_cluster):
    run_command(f'./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'./resume_cluster.sh {cluster.ocm_account} {cluster.id}')

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



    clusters = []

    get_all_cluster_details(args.ocm_account, clusters)

    available_clusters = [cluster for cluster in clusters if cluster.type == 'osd' or cluster.type == 'rosa']
    target_cluster = [cluster for cluster in available_clusters if cluster.name == args.cluster_name]
    if len(target_cluster) > 1:
        sys.exit("More than one clusters found with give name.")

    if not target_cluster:
        sys.exit("No cluster found with given name.")

    if len(target_cluster) == 1:
        target_cluster = target_cluster[0]
        ec2_map = get_instances_for_region(target_cluster.region, 'running')
        print('starting to hibernate ', target_cluster.name)
        if target_cluster.hcp == "false":
            if target_cluster.status == "ready":
                hibernate_cluster(target_cluster)
            else:
                print(f'Cluster {target_cluster.name} is not in ready state, please wait for it to be ready and try again')
        else:
            hybernate_hypershift_cluster(target_cluster, ec2_map)
        ca.main()
        print('Hibernated the cluster:')
        print(target_cluster.__dict__)



if __name__ == '__main__':
    main()