import json
import time

import boto3
import os

# need to sync the list with latest status, and resume it only if status is Hibernating

class oc_cluster:
    def __init__(self, cluster_detail):
        self.id = cluster_detail['id']
        self.name = cluster_detail['name']
        self.api_url = cluster_detail['api_url']
        self.ocp_version = cluster_detail['ocp_version']
        self.type = cluster_detail['type']
        self.hcp = cluster_detail['hcp']
        self.cloud_provider = cluster_detail['cloud_provider']
        self.region = cluster_detail['region']
        self.status = cluster_detail['status']
        self.resume_error = ''
        self.ocm_account = cluster_detail['ocm_account']
def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')
def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def get_last_hibernated():
    s3 = boto3.client('s3')
    s3.download_file('rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json', 'hibernated_latest.json')

def worker_node_belongs_to_the_hcp_cluster(ec2_instance:dict, cluster_name:str):
    result = False
    for tag in ec2_instance['Tags']:
        if tag['Key'] == 'api.openshift.com/name' and tag['Value'] == cluster_name:
            result = True
            break
    return result

def resume_hypershift_cluster(cluster:oc_cluster, ec2_map:dict):
    # ec2_map = ec2_instances[cluster.region]

    print([name for name in ec2_map])
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes if worker_node_belongs_to_the_hcp_cluster(ec2_map[worker_node], cluster.name)]
    if len(InstanceIds) > 0:
        print(f'Starting Worker Instances of cluster {cluster.name}', InstanceIds)
        worker_count = len(InstanceIds)
        ec2_client.terminate_instances(InstanceIds=InstanceIds)
        print(f'Done resuming the cluster {cluster.name}')
        time.sleep(5)
    else:
        print(f'Cluster {cluster.name} is already running.')




def hibernate_cluster(cluster: oc_cluster):
    run_command(f'script/./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'script/./resume_cluster.sh {cluster.ocm_account} {cluster.id}')

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
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        ec2_instances[region] = get_instances_for_region(region, current_state)

def main():
    ec2_instances = {}
    get_all_instances(ec2_instances, 'stopped')
    get_last_hibernated()
    clusters_to_resume = []
    clusters = json.load(open('hibernated_latest.json'))
    for cluster in clusters:
        clusters_to_resume.append(oc_cluster(cluster))
    resumed_clusters = []
    for cluster in clusters_to_resume:
        print('starting with', cluster.name, cluster.type)
        if cluster.hcp == "false":
            # resume_cluster(cluster)
            print("OSD or ROSA Classic - ", cluster.name)
        else:
            if cluster.name == 'et-gpu-2':
                resume_hypershift_cluster(cluster, ec2_instances[cluster.region])
            print("Hypershift cluster - ", cluster.name)
        resumed_clusters.append(cluster.__dict__)
        # print(f'Hibernated {cluster.name}')

    print(json.dumps(resumed_clusters, indent=4))


if __name__ == '__main__':
    main()